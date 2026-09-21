import json
import os
from pathlib import Path

import boto3
from botocore.config import Config

from backup_lab.cloud import Cloud
from backup_lab.database import read_rows
from backup_lab.engine import Engine
from backup_lab.storage import Storage


def load_config():
    path = os.environ.get("LAB_CONFIG_FILE", "config.json")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config["source_folder_id"] == config["recovery_folder_id"]:
        raise ValueError("Source and recovery folders must differ")
    if config["resource_ttl_seconds"] <= config["restore_timeout_seconds"] + 600:
        raise ValueError("Resource TTL must leave time for verification and cleanup")
    config["ca_file"] = str(Path(__file__).parent / "certs" / "root.crt")
    return config


def storage(config):
    client = boto3.client(
        "s3",
        endpoint_url="https://storage.yandexcloud.net",
        region_name="ru-central1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(
            connect_timeout=3,
            read_timeout=8,
            retries={"total_max_attempts": 1},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    return Storage(client, config["control_bucket"])


def engine():
    config = load_config()
    return Engine(
        config,
        storage(config),
        Cloud(),
        lambda host: read_rows(host, config, os.environ["DB_PASSWORD"]),
    )
