import ssl
from contextlib import closing

import pg8000.dbapi

from backup_lab.checks import FIELDS


def connect(host, config, password):
    context = ssl.create_default_context(cafile=config["ca_file"])
    return pg8000.dbapi.connect(
        host=host,
        port=6432,
        database=config["database"],
        user=config["database_user"],
        password=password,
        ssl_context=context,
        timeout=10,
    )


def read_rows(host, config, password):
    with closing(connect(host, config, password)) as db:
        cursor = db.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '10000ms'")
        cursor.execute(
            "SELECT id, amount_cents, bucket, object_key, version_id, sha256 "
            "FROM demo.orders ORDER BY id LIMIT %s",
            (config.get("max_rows", 100) + 1,),
        )
        rows = [dict(zip(FIELDS, values, strict=True)) for values in cursor.fetchall()]
        db.rollback()
        return rows
