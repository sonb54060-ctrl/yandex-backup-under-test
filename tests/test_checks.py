import copy

import pytest
from botocore.exceptions import ClientError

from backup_lab.checks import dataset_hash, verify
from backup_lab.report import render
from backup_lab.storage import Conflict


def test_overwrite_and_delete_marker_preserve_exact_version(fixture):
    s3, store, rows, checkpoint = fixture
    for action in ("overwrite", "delete"):
        if action == "overwrite":
            s3.put_object(Bucket="lab-files", Key="demo/1.txt", Body=b"new content")
        else:
            s3.delete_object(Bucket="lab-files", Key="demo/1.txt")
        assert all(c["ok"] for c in verify(rows, checkpoint, store, "lab-files"))


def test_missing_version_fails_even_with_existing_current_object(fixture):
    s3, store, rows, checkpoint = fixture
    s3.put_object(Bucket="lab-files", Key="demo/1.txt", Body=b"new content")
    s3.delete_object(Bucket="lab-files", Key="demo/1.txt", VersionId=rows[0]["version_id"])
    checks = verify(rows, checkpoint, store, "lab-files")
    assert checks[-1] == {"name": "attachment:1", "ok": False, "detail": "MISSING_VERSION"}


def test_denied_access_distinguished_from_missing(fixture, monkeypatch):
    s3, store, rows, checkpoint = fixture

    def deny(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    monkeypatch.setattr(s3, "get_object", deny)
    assert verify(rows, checkpoint, store, "lab-files")[-1]["detail"] == "ACCESS_DENIED"


@pytest.mark.parametrize("field,value", [("amount_cents", 999), ("id", 7), ("sha256", "0" * 64)])
def test_corrupted_restored_data_detected(fixture, field, value):
    _, store, rows, checkpoint = fixture
    rows = copy.deepcopy(rows)
    rows[0][field] = value
    assert not all(c["ok"] for c in verify(rows, checkpoint, store, "lab-files"))


def test_empty_data_cannot_pass(fixture):
    _, store, _, _ = fixture
    cp = {"row_count": 0, "amount_cents": 0, "dataset_sha256": dataset_hash([])}
    assert not all(c["ok"] for c in verify([], cp, store, "lab-files"))


def test_scope_and_size_limits(fixture):
    _, store, rows, checkpoint = fixture
    assert (
        verify(rows, checkpoint, store, "other-bucket")[-1]["detail"]
        == "REFERENCE_OUTSIDE_DEMO_SCOPE"
    )
    assert (
        verify(rows, checkpoint, store, "lab-files", max_bytes=1)[-1]["detail"]
        == "OBJECT_TOO_LARGE"
    )
    assert (
        verify(rows, checkpoint, store, "lab-files", deadline=0)[-1]["name"]
        == "verification_deadline"
    )


def test_missing_version_reference_never_falls_back(fixture):
    _, store, rows, checkpoint = fixture
    rows[0]["version_id"] = "null"
    assert verify(rows, checkpoint, store, "lab-files")[-1]["detail"] == "UNVERSIONED_REFERENCE"


def test_compare_and_swap_prevents_lost_update(fixture):
    _, store, _, _ = fixture
    etag = store.write_json("control/test", {"counter": 1}, conditional=True)
    with pytest.raises(Conflict):
        store.write_json("control/test", {"counter": 2}, conditional=True)
    store.write_json("control/test", {"counter": 3}, etag, conditional=True)
    with pytest.raises(Conflict):
        store.write_json("control/test", {"counter": 4}, etag, conditional=True)
    assert store.read_json("control/test")[0] == {"counter": 3}


def test_report_escapes_untrusted_values():
    html = render(
        {
            "run_id": "<script>alert(1)</script>",
            "result": "FAIL",
            "checks": [{"name": "<img>", "ok": False, "detail": '<iframe src="x">'}],
        }
    )
    assert "<script>" not in html and "<iframe" not in html and "&lt;img&gt;" in html
