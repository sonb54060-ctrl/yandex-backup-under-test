"""Private, operator-only setup function. The recovery controller cannot call it."""

import hashlib
import os
from contextlib import closing
from datetime import datetime, timezone

from backup_lab.checks import dataset_hash
from backup_lab.cloud import Cloud
from backup_lab.database import connect, read_rows
from backup_lab.runtime import load_config, storage


def handler(event, context):
    c = load_config()
    store, cloud = storage(c), Cloud()
    host = next(h["name"] for h in cloud.hosts(c["source_cluster_id"]) if h.get("role") == "MASTER")
    action = event.get("action")
    if action == "seed":
        if event.get("confirm") != c["lab_id"]:
            raise ValueError("Explicit lab_id confirmation required")
        with closing(connect(host, c, os.environ["DB_PASSWORD"])) as db:
            cursor = db.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS demo")
            cursor.execute("""CREATE TABLE IF NOT EXISTS demo.orders (
                id integer PRIMARY KEY, amount_cents bigint NOT NULL CHECK (amount_cents > 0),
                bucket text NOT NULL, object_key text NOT NULL, version_id text NOT NULL,
                sha256 char(64) NOT NULL)""")
            cursor.execute("SELECT count(*) FROM demo.orders")
            if cursor.fetchone()[0]:
                raise ValueError("Seed is allowed only on an empty demo table")
            for order_id in range(1, 11):
                data = f"Synthetic order {order_id}, amount {order_id * 1000} kopecks\n".encode()
                key = f"demo/order-{order_id}.txt"
                response = store.client.put_object(
                    Bucket=c["attachments_bucket"], Key=key, Body=data
                )
                version = response.get("VersionId")
                if not version or version == "null":
                    raise ValueError("Bucket versioning is required")
                cursor.execute(
                    "INSERT INTO demo.orders VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        order_id,
                        order_id * 1000,
                        c["attachments_bucket"],
                        key,
                        version,
                        hashlib.sha256(data).hexdigest(),
                    ),
                )
            db.commit()
        rows = read_rows(host, c, os.environ["DB_PASSWORD"])
        manifest = {
            "source_cluster_id": c["source_cluster_id"],
            "row_count": len(rows),
            "amount_cents": sum(r["amount_cents"] for r in rows),
            "dataset_sha256": dataset_hash(rows),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        store.write_json("control/seed.json", manifest)
        return {"seeded": len(rows), "next": "Create a manual backup; keep demo data unchanged"}
    if action == "checkpoint":
        seed, _ = store.read_json("control/seed.json")
        if not seed:
            raise ValueError("Seed required")
        backup = next(
            (b for b in cloud.backups(c["source_cluster_id"]) if b["id"] == event["backup_id"]),
            None,
        )
        if not backup:
            raise ValueError("Backup does not belong to the source")
        if backup.get("type") != "MANUAL" or backup.get("status") != "DONE":
            raise ValueError("A completed manual backup is required")
        captured = datetime.fromisoformat(seed["captured_at"])
        started = datetime.fromisoformat(backup["startedAt"])
        if started < captured:
            raise ValueError("Backup predates the seeded data")
        if dataset_hash(read_rows(host, c, os.environ["DB_PASSWORD"])) != seed["dataset_sha256"]:
            raise ValueError("Source changed since seeding; recreate a quiescent checkpoint")
        seed["backup_id"] = backup["id"]
        store.write_json("control/checkpoint.json", seed)
        return {"checkpoint": "READY", "backup_id": backup["id"]}
    if action in {"overwrite", "delete-marker", "remove-version", "mutate-order"}:
        if event.get("confirm") != c["lab_id"]:
            raise ValueError("Explicit lab_id confirmation required")
        checkpoint, _ = store.read_json("control/checkpoint.json")
        if not checkpoint:
            raise ValueError("Finalize the checkpoint before changing demo data")
        rows = read_rows(host, c, os.environ["DB_PASSWORD"])
        row = next(r for r in rows if r["id"] == 1)
        if row["bucket"] != c["attachments_bucket"] or row["object_key"] != "demo/order-1.txt":
            raise ValueError("Unexpected synthetic fixture reference")
        args = {"Bucket": c["attachments_bucket"], "Key": row["object_key"]}
        if action == "mutate-order":
            with closing(connect(host, c, os.environ["DB_PASSWORD"])) as db:
                db.cursor().execute("UPDATE demo.orders SET amount_cents = 999999 WHERE id = 1")
                db.commit()
        elif action == "overwrite":
            store.client.put_object(**args, Body=b"Changed AFTER the checkpoint\n")
        elif action == "delete-marker":
            store.client.delete_object(**args)
        else:
            # Only the version referenced by synthetic order 1 can be removed.
            store.client.delete_object(**args, VersionId=row["version_id"])
        return {"changed": action, "order_id": 1}
    raise ValueError("Unsupported setup action")
