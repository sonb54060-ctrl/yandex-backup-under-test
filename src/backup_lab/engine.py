"""Durable short-step controller with CAS ownership and conservative recovery of lost replies.

The lease is longer than the deployed function's hard timeout. An ambiguous restore
is reconciled by labels; it is NEVER submitted a second time. A stalled run keeps
its global lock until cleanup is confirmed or an operator investigates it.
"""

import copy
import re
import time
from datetime import datetime

from backup_lab.checks import verify
from backup_lab.cloud import CloudError
from backup_lab.report import render
from backup_lab.storage import Conflict

TERMINAL = {"DONE"}
LEASE_SECONDS = 180


def validate_run_id(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", run_id):
        raise ValueError("run_id must contain 1–40 lowercase letters, digits or hyphens")


class Engine:
    def __init__(self, config, store, cloud, read_rows, clock=time.time):
        self.config, self.store, self.cloud = config, store, cloud
        self.read_rows, self.clock = read_rows, clock
        self.key = "control/active.json"

    def response(self, state):
        return {
            k: state[k] for k in ("run_id", "phase", "result", "cleanup", "error") if k in state
        }

    def start(self, run_id):
        validate_run_id(run_id)
        previous, _ = self.store.read_json(f"reports/{run_id}.json")
        if previous and previous.get("phase") == "DONE":
            return self.response(previous)
        current, etag = self.store.read_json(self.key)
        if current and current["run_id"] == run_id:
            return self.response(current)
        if current and current["phase"] not in TERMINAL:
            return {"run_id": run_id, "phase": "BUSY", "result": "NOT_RUN"}
        state = {
            "run_id": run_id,
            "phase": "NEW",
            "started_at": self.clock(),
            "mode": "YANDEX_CLOUD",
            "result": "PENDING",
            "cleanup": "NOT_STARTED",
            "lease_until": 0,
            "checks": [],
        }
        try:
            self.store.write_json(self.key, state, etag, conditional=True)
        except Conflict:
            return {"run_id": run_id, "phase": "BUSY", "result": "NOT_RUN"}
        return self.response(state)

    def owned(self, cluster, run_id=None):
        c = self.config
        labels = cluster.get("labels", {})
        return (
            cluster.get("folderId") == c["recovery_folder_id"]
            and cluster.get("id") != c["source_cluster_id"]
            and labels.get("managed-by") == "backup-under-test"
            and labels.get("lab-id") == c["lab_id"]
            and labels.get("source") == c["source_cluster_id"]
            and (run_id is None or labels.get("run-id") == run_id)
        )

    def find(self, run_id):
        matches = [
            c
            for c in self.cloud.clusters(self.config["recovery_folder_id"])
            if self.owned(c, run_id)
        ]
        if len(matches) > 1:
            raise RuntimeError("MULTIPLE_OWNED_CLUSTERS_REQUIRE_OPERATOR")
        return matches[0] if matches else None

    def tick(self, run_id):
        validate_run_id(run_id)
        state, etag = self.store.read_json(self.key)
        if not state or state["run_id"] != run_id:
            old, _ = self.store.read_json(f"reports/{run_id}.json")
            return self.response(old) if old else {"run_id": run_id, "phase": "NOT_FOUND"}
        if state["phase"] == "DONE" or state.get("lease_until", 0) > self.clock():
            return self.response(state)
        state = copy.deepcopy(state)
        state["lease_until"] = self.clock() + LEASE_SECONDS
        try:
            etag = self.store.write_json(self.key, state, etag, conditional=True)
        except Conflict:
            return self.response(state)

        def save():
            nonlocal etag
            etag = self.store.write_json(self.key, state, etag, conditional=True)

        try:
            self.advance(state, save)
        except Conflict:
            # A stale writer must not overwrite the successor or release its lease.
            return {"run_id": run_id, "phase": "WAIT"}
        except Exception as exc:
            state["error"] = type(exc).__name__ + ":" + getattr(exc, "code", "STEP_ERROR")
            state["result"] = "ERROR"
            if state["phase"] == "RESTORE_INTENT":
                # Preserve intent until the delayed resource is found or reconciled.
                if isinstance(exc, CloudError) and exc.code in {"400", "401", "403", "404", "429"}:
                    state.update(restore_rejected=True, phase="CLEANUP")
            elif state["phase"] in {"CLEANUP", "DELETING", "MANUAL_REVIEW"}:
                state["cleanup"] = "RETRY_REQUIRED"
                if self.clock() - state.get("cleanup_started_at", self.clock()) > 1800:
                    state["phase"] = "MANUAL_REVIEW"
            else:
                state["phase"] = "CLEANUP"
        state["lease_until"] = 0
        state["updated_at"] = self.clock()
        save()
        self.save_progress(state)
        return self.response(state)

    def save_progress(self, state):
        report = {k: v for k, v in state.items() if k not in {"checkpoint", "lease_until"}}
        if state["phase"] != "DONE":
            report["verification_result"] = report["result"]
            report["result"] = "INCOMPLETE"
        self.store.write_json(f"runs/{state['run_id']}.json", report)
        self.store.write_html(f"runs/{state['run_id']}.html", render(report))

    def advance(self, s, save):
        c, now = self.config, self.clock()
        phase = s["phase"]
        if phase == "NEW":
            checkpoint, _ = self.store.read_json("control/checkpoint.json")
            if not checkpoint or checkpoint["source_cluster_id"] != c["source_cluster_id"]:
                raise ValueError("CHECKPOINT_REQUIRED")
            if checkpoint["row_count"] < 1 or checkpoint["row_count"] > c.get("max_rows", 100):
                raise ValueError("CHECKPOINT_OUT_OF_BOUNDS")
            backup = next(
                (
                    b
                    for b in self.cloud.backups(c["source_cluster_id"])
                    if b["id"] == checkpoint["backup_id"]
                ),
                None,
            )
            if backup is None:
                raise ValueError("BACKUP_NOT_FOUND_ON_SOURCE")
            if backup.get("type") != "MANUAL" or backup.get("status") != "DONE":
                raise ValueError("COMPLETED_MANUAL_BACKUP_REQUIRED")
            s.update(
                checkpoint=checkpoint,
                backup_id=backup["id"],
                restore_started_at=now,
                phase="RESTORE_INTENT",
            )
            save()  # Durable intent BEFORE the side effect, even if the response is lost.
            body = {
                "backupId": backup["id"],
                "name": f"{c['lab_id']}-{s['run_id']}",
                "folderId": c["recovery_folder_id"],
                "networkId": c["recovery_network_id"],
                "environment": "PRODUCTION",
                "deletionProtection": False,
                "securityGroupIds": [c["recovery_security_group_id"]],
                "labels": {
                    "managed-by": "backup-under-test",
                    "lab-id": c["lab_id"],
                    "source": c["source_cluster_id"],
                    "run-id": s["run_id"],
                    "expires-at": str(int(now + c.get("resource_ttl_seconds", 7200))),
                },
                "configSpec": {
                    "version": c["postgres_version"],
                    "resources": {
                        "resourcePresetId": c["resource_preset_id"],
                        "diskTypeId": "network-ssd",
                        "diskSize": str(c["disk_size_gb"] * 1024**3),
                    },
                },
                "hostSpecs": [
                    {
                        "zoneId": c["zone"],
                        "subnetId": c["recovery_subnet_id"],
                        "assignPublicIp": False,
                    }
                ],
            }
            op = self.cloud.restore(body)
            s["restore_operation_id"] = op["id"]
            s["cluster_id"] = op.get("metadata", {}).get("clusterId", "")
            s["phase"] = "RESTORING"
        elif phase == "RESTORE_INTENT":
            cluster = self.find(s["run_id"])
            if cluster:
                s["cluster_id"] = cluster["id"]
                s["adopted_cluster"] = True
                s["phase"] = "CLEANUP"  # Ambiguous run cannot silently become PASS.
                s["result"] = "ERROR"
                s["error"] = "RESTORE_RESPONSE_LOST"
            elif now - s["restore_started_at"] > c.get("restore_timeout_seconds", 3600):
                s.update(
                    phase="MANUAL_REVIEW",
                    result="ERROR",
                    cleanup="UNCONFIRMED",
                    error="RESTORE_OUTCOME_UNKNOWN",
                )
        elif phase == "RESTORING":
            op = self.cloud.operation(s["restore_operation_id"])
            s["cluster_id"] = s.get("cluster_id") or op.get("metadata", {}).get("clusterId", "")
            if op.get("done"):
                if op.get("error"):
                    s.update(result="ERROR", error="RESTORE_OPERATION_FAILED", phase="CLEANUP")
                else:
                    s["cluster_id"] = s.get("cluster_id") or op.get("response", {}).get("id", "")
                    s["restore_seconds"] = round(now - s["restore_started_at"], 3)
                    s["phase"] = "VERIFY"
            elif now - s["restore_started_at"] > c.get("restore_timeout_seconds", 3600):
                s.update(result="ERROR", error="RESTORE_TIMEOUT", phase="CLEANUP")
        elif phase == "VERIFY":
            cluster = self.cloud.cluster(s["cluster_id"])
            if not cluster or not self.owned(cluster, s["run_id"]):
                raise ValueError("RESTORE_TARGET_NOT_OWNED")
            hosts = self.cloud.hosts(s["cluster_id"])
            host = next(h["name"] for h in hosts if h.get("role") == "MASTER")
            started = time.monotonic()
            rows = self.read_rows(host)
            s["checks"] = verify(
                rows,
                s["checkpoint"],
                self.store,
                c["attachments_bucket"],
                max_rows=c.get("max_rows", 100),
                deadline=started + 45,
            )
            s["verification_seconds"] = round(time.monotonic() - started, 3)
            recovered = datetime.fromisoformat(
                s["checkpoint"]["captured_at"].replace("Z", "+00:00")
            )
            s["recovered_data_age_seconds"] = round(now - recovered.timestamp(), 3)
            s["checks"].append(
                {
                    "name": "recovery_time_target",
                    "ok": self.clock() - s["restore_started_at"]
                    <= c.get("rto_target_seconds", 3600),
                    "detail": "Время восстановления и проверки в пределах цели",
                }
            )
            s["result"] = "PASS" if all(x["ok"] for x in s["checks"]) else "FAIL"
            s["phase"] = "CLEANUP"
        elif phase in {"CLEANUP", "DELETING", "MANUAL_REVIEW"}:
            self.cleanup(s)

    def cleanup(self, s):
        s.setdefault("cleanup_started_at", self.clock())
        cluster = (
            self.cloud.cluster(s["cluster_id"]) if s.get("cluster_id") else self.find(s["run_id"])
        )
        if cluster:
            if not self.owned(cluster, s["run_id"]):
                s.update(
                    phase="MANUAL_REVIEW",
                    result="ERROR",
                    cleanup="REFUSED",
                    error="UNOWNED_CLEANUP_TARGET",
                )
                return
            s["cluster_id"] = cluster["id"]
            if cluster.get("status") != "DELETING":
                self.cloud.delete(cluster["id"])
            s.update(phase="DELETING", cleanup="PENDING")
            if self.clock() - s["cleanup_started_at"] > 1800:
                s.update(phase="MANUAL_REVIEW", result="ERROR", error="CLEANUP_TIMEOUT")
            return
        # A pending restore can create a resource after a temporary 404/list miss.
        if s.get("restore_operation_id"):
            op = self.cloud.operation(s["restore_operation_id"])
            if not op.get("done"):
                s.update(cleanup="WAITING_FOR_RESTORE", phase="CLEANUP")
                return
        if (
            s.get("restore_started_at")
            and not s.get("restore_operation_id")
            and not s.get("adopted_cluster")
            and not s.get("restore_rejected")
        ):
            s.update(phase="MANUAL_REVIEW", result="ERROR", cleanup="UNCONFIRMED")
            return
        self.finish(s)

    def finish(self, s):
        s.update(phase="DONE", cleanup="CONFIRMED_ABSENT", finished_at=self.clock())
        s["cleanup_seconds"] = round(self.clock() - s.get("cleanup_started_at", self.clock()), 3)
        if s["result"] == "PENDING":
            s["result"] = "ERROR"
        report = {k: v for k, v in s.items() if k not in {"checkpoint", "lease_until"}}
        # Finish is committed only after both private artifacts are written.
        self.store.write_html(f"reports/{s['run_id']}.html", render(report))
        self.store.write_json(f"reports/{s['run_id']}.json", report)

    def janitor(self):
        """An independently scheduled sweep; never touch resources lacking our exact labels."""
        outcomes = []
        for cluster in self.cloud.clusters(self.config["recovery_folder_id"]):
            if not self.owned(cluster):
                continue
            expiry = cluster.get("labels", {}).get("expires-at", "")
            if not expiry.isdigit() or int(expiry) > self.clock():
                continue
            try:
                if cluster.get("status") != "DELETING":
                    self.cloud.delete(cluster["id"])
                outcomes.append({"cluster_id": cluster["id"], "cleanup": "REQUESTED"})
            except Exception as exc:
                outcomes.append(
                    {"cluster_id": cluster["id"], "cleanup": "ERROR", "error": type(exc).__name__}
                )
        state, etag = self.store.read_json(self.key)
        if state and state["phase"] != "DONE" and state.get("lease_until", 0) <= self.clock():
            if self.clock() - state["started_at"] > self.config.get("resource_ttl_seconds", 7200):
                # Recovery of interrupted workflows is deliberately limited to cleanup.
                state["result"] = "ERROR"
                state["error"] = "RUN_EXPIRED"
                if state["phase"] not in {"RESTORE_INTENT", "MANUAL_REVIEW"}:
                    state["phase"] = "CLEANUP"
                try:
                    self.store.write_json(self.key, state, etag, conditional=True)
                    outcomes.append(self.tick(state["run_id"]))
                except Conflict:
                    pass
        return {"outcomes": outcomes}
