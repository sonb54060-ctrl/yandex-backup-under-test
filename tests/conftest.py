import hashlib
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from backup_lab.checks import dataset_hash
from backup_lab.storage import Storage


@pytest.fixture
def fixture():
    with mock_aws():
        s3 = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
        )
        for name in ("lab-files", "lab-control"):
            s3.create_bucket(Bucket=name)
        s3.put_bucket_versioning(Bucket="lab-files", VersioningConfiguration={"Status": "Enabled"})
        body = b"original attachment"
        version = s3.put_object(Bucket="lab-files", Key="demo/1.txt", Body=body)["VersionId"]
        rows = [
            {
                "id": 1,
                "amount_cents": 1500,
                "bucket": "lab-files",
                "object_key": "demo/1.txt",
                "version_id": version,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        ]
        checkpoint = {
            "source_cluster_id": "source",
            "backup_id": "backup-1",
            "row_count": 1,
            "amount_cents": 1500,
            "dataset_sha256": dataset_hash(rows),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        store = Storage(s3, "lab-control")
        store.write_json("control/checkpoint.json", checkpoint)
        yield s3, store, rows, checkpoint
