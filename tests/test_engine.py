import copy
import time

import pytest

from backup_lab.cloud import CloudError
from backup_lab.engine import Engine


class FakeCloud:
    def __init__(self):
        self.resources = {}
        self.restore_calls = 0
        self.deleted = []
        self.done = True
        self.lost_reply = False
        self.quota_error = False
        self.operation_error = False

    def backups(self, source):
        return [{"id": "backup-1", "type": "MANUAL", "status": "DONE"}]

    def restore(self, body):
        self.restore_calls += 1
        if self.quota_error:
            raise CloudError(429)
        self.resources["clone"] = dict(copy.deepcopy(body), id="clone", status="RUNNING")
        if self.lost_reply:
            raise TimeoutError("response lost")
        return {"id": "operation-1", "metadata": {"clusterId": "clone"}}

    def operation(self, operation_id):
        result = {"done": self.done, "metadata": {"clusterId": "clone"}}
        if self.operation_error:
            result["error"] = {"code": 8}
        return result

    def cluster(self, cluster_id):
        return self.resources.get(cluster_id)

    def clusters(self, folder):
        return [c for c in self.resources.values() if c["folderId"] == folder]

    def hosts(self, cluster_id):
        return [{"name": "clone.internal", "role": "MASTER"}]

    def delete(self, cluster_id):
        self.deleted.append(cluster_id)
        self.resources.pop(cluster_id, None)
        return {"id": "delete-op"}


@pytest.fixture
def lab(fixture):
    _, store, rows, _ = fixture
    config = {
        "lab_id": "test-lab",
        "source_cluster_id": "source",
        "recovery_folder_id": "recovery",
        "recovery_network_id": "network",
        "recovery_subnet_id": "subnet",
        "recovery_security_group_id": "sg",
        "zone": "ru-central1-b",
        "postgres_version": "16",
        "resource_preset_id": "s3-c2-m8",
        "disk_size_gb": 10,
        "attachments_bucket": "lab-files",
        "restore_timeout_seconds": 3600,
        "resource_ttl_seconds": 7200,
    }
    cloud = FakeCloud()
    clock = [time.time()]
    engine = Engine(config, store, cloud, lambda host: copy.deepcopy(rows), lambda: clock[0])
    return engine, cloud, clock, store


def drive(engine, run_id="test", steps=8):
    result = engine.start(run_id)
    for _ in range(steps):
        result = engine.tick(run_id)
        if result["phase"] == "DONE":
            break
    return result


def test_full_run_and_completed_duplicate(lab):
    engine, cloud, _, store = lab
    result = drive(engine)
    assert result == {
        "run_id": "test",
        "phase": "DONE",
        "result": "PASS",
        "cleanup": "CONFIRMED_ABSENT",
    }
    assert cloud.deleted == ["clone"]
    assert engine.start("test") == result
    assert cloud.restore_calls == 1
    assert store.read_json("reports/test.json")[0]["mode"] == "YANDEX_CLOUD"


def test_second_run_blocked_and_duplicate_tick_leased(lab):
    engine, cloud, clock, store = lab
    engine.start("one")
    assert engine.start("two")["phase"] == "BUSY"
    state, etag = store.read_json(engine.key)
    state["lease_until"] = clock[0] + 180
    store.write_json(engine.key, state, etag, conditional=True)
    engine.tick("one")
    assert cloud.restore_calls == 0


def test_lost_restore_reply_adopt_and_cleanup_without_resubmission(lab):
    engine, cloud, _, _ = lab
    cloud.lost_reply = True
    result = drive(engine)
    assert result["phase"] == "DONE" and result["result"] == "ERROR"
    assert cloud.restore_calls == 1 and cloud.deleted == ["clone"]


def test_quota_rejection_fails_without_creating_resources(lab):
    engine, cloud, clock, _ = lab
    cloud.quota_error = True
    result = drive(engine)
    assert result["phase"] == "DONE"
    assert result["result"] == "ERROR"
    assert not cloud.deleted
    assert cloud.restore_calls == 1


def test_unknown_response_without_visible_cluster_keeps_lock(lab):
    engine, cloud, clock, store = lab
    cloud.lost_reply = True
    engine.start("test")
    engine.tick("test")
    cloud.resources.clear()
    clock[0] += 3700
    result = engine.tick("test")
    assert result["phase"] == "MANUAL_REVIEW"
    assert engine.start("second")["phase"] == "BUSY"
    assert store.read_json("runs/test.json")[0]["result"] == "INCOMPLETE"
    assert cloud.restore_calls == 1


def test_restore_operation_failure_never_passes(lab):
    engine, cloud, _, _ = lab
    cloud.operation_error = True
    result = drive(engine)
    assert result["result"] == "ERROR" and result["cleanup"] == "CONFIRMED_ABSENT"


def test_verification_failure_still_cleans_up(lab, fixture):
    engine, cloud, _, _ = lab
    s3, _, rows, _ = fixture
    s3.delete_object(Bucket="lab-files", Key="demo/1.txt", VersionId=rows[0]["version_id"])
    result = drive(engine)
    assert result["result"] == "FAIL" and cloud.deleted == ["clone"]


def test_timeout_waits_for_late_restore_instead_of_reporting_cleanup(lab):
    engine, cloud, clock, _ = lab
    cloud.done = False
    engine.start("test")
    engine.tick("test")
    cloud.resources.clear()
    clock[0] += 3700
    engine.tick("test")
    result = engine.tick("test")
    assert result["phase"] == "CLEANUP" and result["cleanup"] == "WAITING_FOR_RESTORE"
    assert result["result"] == "ERROR"


def test_delete_source_or_unowned_cluster_refused(lab):
    engine, cloud, _, store = lab
    engine.start("test")
    engine.tick("test")
    cloud.resources["clone"]["labels"]["lab-id"] = "other"
    state, etag = store.read_json(engine.key)
    state["phase"] = "CLEANUP"
    store.write_json(engine.key, state, etag, conditional=True)
    result = engine.tick("test")
    assert result["phase"] == "MANUAL_REVIEW" and not cloud.deleted
    owned_labels = {"managed-by": "backup-under-test", "lab-id": "test-lab", "source": "source"}
    assert not engine.owned({"id": "source", "folderId": "recovery", "labels": owned_labels})


def test_janitor_only_removes_expired_owned_resources(lab):
    engine, cloud, clock, _ = lab
    engine.start("test")
    engine.tick("test")
    other = copy.deepcopy(cloud.resources["clone"])
    other["id"] = "unrelated"
    other["labels"]["lab-id"] = "different-lab"
    cloud.resources["unrelated"] = other
    engine.janitor()
    assert not cloud.deleted
    clock[0] += 7300
    engine.janitor()
    assert cloud.deleted == ["clone"] and "unrelated" in cloud.resources


def test_checkpoint_missing_fails_before_restore(lab):
    engine, cloud, _, store = lab
    store.client.delete_object(Bucket="lab-control", Key="control/checkpoint.json")
    result = drive(engine)
    assert result["result"] == "ERROR" and cloud.restore_calls == 0


@pytest.mark.parametrize("kind,status", [("AUTOMATED", "DONE"), ("MANUAL", "CREATING")])
def test_unfinished_or_automated_backup_rejected(lab, kind, status):
    engine, cloud, _, _ = lab
    cloud.backups = lambda source: [{"id": "backup-1", "type": kind, "status": status}]
    assert drive(engine)["result"] == "ERROR"
    assert cloud.restore_calls == 0


def test_cleanup_permission_failure_requires_operator_after_deadline(lab):
    engine, cloud, clock, _ = lab

    def denied(cluster_id):
        raise CloudError(403)

    cloud.delete = denied
    drive(engine)
    clock[0] += 1801
    result = engine.tick("test")
    assert result["phase"] == "MANUAL_REVIEW"
    assert result["cleanup"] == "RETRY_REQUIRED"
    assert engine.start("another")["phase"] == "BUSY"
