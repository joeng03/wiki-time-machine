"""MariaDB connections. Every session is pinned to UTC."""
from __future__ import annotations

import contextlib
from typing import Iterator

import pymysql
import pymysql.cursors

from .config import Settings, settings as load_settings

MIN_VERSION = (11, 8)


def connect(role: str = "reader", cfg: Settings | None = None, database: str | None = None,
            insert_history: bool = False) -> pymysql.connections.Connection:
    cfg = cfg or load_settings()
    if role == "reader":
        user, password = cfg.reader_user, cfg.reader_password
    elif role == "importer":
        user, password = cfg.importer_user, cfg.importer_password
    else:
        raise ValueError(role)
    conn = pymysql.connect(
        host=cfg.db_host, port=cfg.db_port, user=user, password=password,
        database=database or cfg.db_name, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
        if insert_history:
            # Only the dedicated importer session may write explicit system periods.
            cur.execute("SET SESSION system_versioning_insert_history = ON")
    return conn


@contextlib.contextmanager
def session(role: str = "reader", **kw) -> Iterator[pymysql.connections.Connection]:
    conn = connect(role, **kw)
    try:
        yield conn
    finally:
        conn.close()


def server_capabilities(conn) -> dict:
    """Verify the server supports what we depend on (called at startup and by `wtm doctor`)."""
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION() AS v, @@secure_timestamp AS st, @@time_zone AS tz")
        row = cur.fetchone()
        cur.execute("SHOW VARIABLES LIKE 'system_versioning_insert_history'")
        has_ih = cur.fetchone() is not None
        try:
            cur.execute("SELECT VEC_DISTANCE_COSINE(VEC_FromText('[1,0]'), VEC_FromText('[0,1]')) AS d")
            has_vec = cur.fetchone()["d"] is not None
        except pymysql.MySQLError:
            has_vec = False
    version = row["v"]
    nums = tuple(int(x) for x in version.split("-")[0].split(".")[:2])
    return {
        "version": version,
        "version_ok": nums >= MIN_VERSION,
        "secure_timestamp": row["st"],
        "insert_history_supported": has_ih and row["st"] in ("NO", "REPLICATION"),
        "vector_supported": has_vec,
        "session_time_zone": row["tz"],
    }


def apply_schema(conn, sql_path: str) -> None:
    with open(sql_path, encoding="utf-8") as fh:
        text = fh.read()
    statements = []
    buf = []
    for line in text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    with conn.cursor() as cur:
        for stmt in statements:
            if stmt.strip():
                cur.execute(stmt)
