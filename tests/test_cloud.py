import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from backup_lab.cloud import Cloud, CloudError


def test_restore_api_uses_post_and_does_not_retry(monkeypatch):
    seen = []
    monkeypatch.setenv("YC_TOKEN", "test-token")

    def send(request, timeout):
        seen.append(request)
        raise HTTPError(request.full_url, 503, "unavailable", {}, BytesIO(b"private data"))

    monkeypatch.setattr("backup_lab.cloud.urlopen", send)
    with pytest.raises(CloudError, match="CLOUD_503") as exc:
        Cloud().restore({"backupId": "backup"})
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].full_url.endswith("/managed-postgresql/v1/clusters:restore")
    assert json.loads(seen[0].data) == {"backupId": "backup"}
    assert "private" not in str(exc.value)


def test_paginated_listing_follows_next_token(monkeypatch):
    cloud = Cloud()
    paths = []

    def request(method, path):
        paths.append(path)
        return (
            {"backups": [{"id": "one"}], "nextPageToken": "next/+"}
            if len(paths) == 1
            else {"backups": [{"id": "two"}]}
        )

    monkeypatch.setattr(cloud, "request", request)
    assert [b["id"] for b in cloud.backups("source")] == ["one", "two"]
    assert "next%2F%2B" in paths[1]


def test_permission_error_is_not_treated_as_missing_cluster(monkeypatch):
    cloud = Cloud()

    def deny(*args):
        raise CloudError(403)

    monkeypatch.setattr(cloud, "request", deny)
    with pytest.raises(CloudError):
        cloud.cluster("clone")


def test_operation_polling_uses_the_dedicated_operation_endpoint(monkeypatch):
    seen = []
    monkeypatch.setenv("YC_TOKEN", "test-token")

    def send(request, timeout):
        seen.append(request.full_url)
        return BytesIO(b'{"done": true, "metadata": {"clusterId": "clone"}}')

    monkeypatch.setattr("backup_lab.cloud.urlopen", send)
    assert Cloud().operation("operation-1")["done"]
    assert seen == ["https://operation.api.cloud.yandex.net/operations/operation-1"]
