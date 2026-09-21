from unittest.mock import MagicMock

from backup_lab.database import read_rows


def test_reader_uses_read_only_transaction_and_bound_limit(monkeypatch):
    database = MagicMock()
    cursor = database.cursor.return_value
    cursor.fetchall.return_value = [(1, 1500, "bucket", "demo/file", "v1", "hash")]
    monkeypatch.setattr("backup_lab.database.connect", lambda *args: database)
    rows = read_rows("private-host", {"max_rows": 10}, "not-logged")
    calls = cursor.execute.call_args_list
    assert calls[0].args == ("SET TRANSACTION READ ONLY",)
    assert calls[1].args == ("SET LOCAL statement_timeout = '10000ms'",)
    assert calls[2].args[1] == (11,)
    assert rows[0]["version_id"] == "v1"
    database.rollback.assert_called_once()
    database.close.assert_called_once()
