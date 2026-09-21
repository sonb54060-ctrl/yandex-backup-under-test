"""Pure checks, shared by the cloud run and the local demonstration."""

import hashlib
import json
import time

from backup_lab.storage import ObjectProblem

FIELDS = ("id", "amount_cents", "bucket", "object_key", "version_id", "sha256")


def dataset_hash(rows):
    canonical = [
        {field: row[field] for field in FIELDS} for row in sorted(rows, key=lambda r: r["id"])
    ]
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify(
    rows,
    checkpoint,
    storage,
    allowed_bucket,
    *,
    max_rows=100,
    max_bytes=1024 * 1024,
    deadline=None,
    clock=time.monotonic,
):
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("nonempty_dataset", bool(rows), "Восстановлены заказы")
    check("row_count", len(rows) == checkpoint["row_count"], f"Получено {len(rows)} записей")
    check(
        "amount_total",
        sum(r["amount_cents"] for r in rows) == checkpoint["amount_cents"],
        "Сумма заказов соответствует контрольной точке",
    )
    check("unique_ids", len({r["id"] for r in rows}) == len(rows), "Идентификаторы уникальны")
    check(
        "dataset_digest",
        dataset_hash(rows) == checkpoint["dataset_sha256"],
        "Записи и ссылки на вложения соответствуют контрольной точке",
    )
    if len(rows) > max_rows:
        check("bounded_dataset", False, "Слишком много записей для учебного стенда")
        return checks
    for row in rows:
        name = f"attachment:{row['id']}"
        if deadline is not None and clock() >= deadline:
            check("verification_deadline", False, "Проверка не закончена за отведенное время")
            break
        if row["bucket"] != allowed_bucket or not row["object_key"].startswith("demo/"):
            check(name, False, "REFERENCE_OUTSIDE_DEMO_SCOPE")
            continue
        try:
            digest = storage.attachment_hash(
                row["bucket"], row["object_key"], row["version_id"], max_bytes
            )
            check(
                name,
                digest == row["sha256"],
                "VERSION_AND_HASH_MATCH" if digest == row["sha256"] else "HASH_MISMATCH",
            )
        except ObjectProblem as exc:
            check(name, False, exc.code)
    return checks
