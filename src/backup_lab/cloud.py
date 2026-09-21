"""Small REST adapter. Mutating requests are never retried implicitly."""

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CloudError(Exception):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(f"CLOUD_{code}")


def iam_token():
    if os.environ.get("YC_TOKEN"):
        return os.environ["YC_TOKEN"]
    request = Request(
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urlopen(request, timeout=3) as response:
        return json.load(response)["access_token"]


class Cloud:
    base = "https://mdb.api.cloud.yandex.net/managed-postgresql/v1"

    def request(self, method, path, body=None, *, base=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            (base or self.base) + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + iam_token(),
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as exc:
            # API response text can include user data. Keep only the HTTP status in reports.
            raise CloudError(exc.code) from exc

    def paged(self, path, field):
        items, token = [], ""
        while True:
            separator = "&" if "?" in path else "?"
            page = self.request(
                "GET", path + separator + urlencode({"pageSize": 100, "pageToken": token})
            )
            items.extend(page.get(field, []))
            token = page.get("nextPageToken", "")
            if not token:
                return items

    def cluster(self, cluster_id):
        try:
            return self.request("GET", f"/clusters/{cluster_id}")
        except CloudError as exc:
            if exc.code == "404":
                return None
            raise

    def clusters(self, folder):
        return self.paged("/clusters?" + urlencode({"folderId": folder}), "clusters")

    def backups(self, cluster_id):
        return self.paged(f"/clusters/{cluster_id}/backups", "backups")

    def hosts(self, cluster_id):
        return self.paged(f"/clusters/{cluster_id}/hosts", "hosts")

    def operation(self, operation_id):
        return self.request(
            "GET", f"/operations/{operation_id}", base="https://operation.api.cloud.yandex.net"
        )

    def restore(self, body):
        return self.request("POST", "/clusters:restore", body)

    def delete(self, cluster_id):
        return self.request("DELETE", f"/clusters/{cluster_id}")
