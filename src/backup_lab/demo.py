"""Local S3 simulation. No Yandex API or real PostgreSQL restoration is involved."""

import hashlib
import json
import time
from pathlib import Path

import boto3
from moto import mock_aws

from backup_lab.checks import dataset_hash, verify
from backup_lab.report import render
from backup_lab.storage import Storage


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with mock_aws():
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
        )
        client.create_bucket(Bucket="lab-files")
        client.create_bucket(Bucket="lab-control")
        client.put_bucket_versioning(
            Bucket="lab-files", VersioningConfiguration={"Status": "Enabled"}
        )
        store = Storage(client, "lab-control")
        rows = []
        for i in range(1, 11):
            body, key = f"Synthetic document {i}".encode(), f"demo/order-{i}.txt"
            version = client.put_object(Bucket="lab-files", Key=key, Body=body)["VersionId"]
            rows.append(
                {
                    "id": i,
                    "amount_cents": i * 1000,
                    "bucket": "lab-files",
                    "object_key": key,
                    "version_id": version,
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        checkpoint = {
            "row_count": len(rows),
            "amount_cents": 55000,
            "dataset_sha256": dataset_hash(rows),
        }
        for scenario in ("intact", "overwritten", "delete-marker", "missing-version"):
            first = rows[0]
            args = {"Bucket": first["bucket"], "Key": first["object_key"]}
            if scenario == "overwritten":
                client.put_object(**args, Body=b"New and different content")
            elif scenario == "delete-marker":
                client.delete_object(**args)
            elif scenario == "missing-version":
                client.delete_object(**args, VersionId=first["version_id"])
            checks = verify(rows, checkpoint, store, "lab-files")
            report = {
                "mode": "LOCAL_SIMULATION_NO_CLOUD_RESTORE",
                "run_id": scenario,
                "result": "PASS" if all(c["ok"] for c in checks) else "FAIL",
                "checks": checks,
                "finished_at": time.time(),
                "cleanup": "IN_MEMORY_ONLY",
            }
            (output / f"{scenario}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output / f"{scenario}.html").write_text(render(report), encoding="utf-8")
            results.append({"scenario": scenario, "result": report["result"]})
    return results
