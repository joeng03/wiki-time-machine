"""Tests run against the real MariaDB container (SVT + VECTOR are the point), in database wtm_test."""
from __future__ import annotations

import hashlib
import os

import pytest

from wtm import api as api_mod
from wtm.db import apply_schema, connect
from wtm.embed import HashEmbedder

TEST_DB = os.environ.get("WTM_TEST_DB", "wtm_test")
SQL = os.path.join(os.environ.get("WTM_SQL_DIR", "/app/sql"), "wtm_schema.sql")
TABLES = ["wiki_page", "wiki_revision", "wiki_fetch_window", "wiki_build", "wiki_active_build", "wiki_coverage",
          "wiki_snapshot", "wiki_revision_text", "wiki_chunk", "wiki_revision_chunk", "corpus_passage"]


@pytest.fixture()
def db():
    """Fresh schema; yields an importer session with system_versioning_insert_history=ON."""
    conn = connect("importer", database=TEST_DB, insert_history=True)
    with conn.cursor() as cur:
        for t in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
    apply_schema(conn, SQL)
    yield conn
    conn.close()


@pytest.fixture()
def reader():
    conn = connect("reader", database=TEST_DB)
    yield conn
    conn.close()


@pytest.fixture()
def embedder():
    return HashEmbedder()


@pytest.fixture()
def client(db, embedder):
    from fastapi.testclient import TestClient
    api_mod.STATE.update({"embedder": embedder, "db_kwargs": {"database": TEST_DB}, "llm": False})
    yield TestClient(api_mod.app)
    api_mod.STATE.update({"embedder": None, "db_kwargs": {}, "llm": None})


def rev(rid: int, ts: str, text: str | None, page_id: int = 1, parent: int | None = None, wiki_id: str = "enwiki") -> dict:
    return {"kind": "revision", "wiki_id": wiki_id, "page_id": page_id, "revision_id": rid, "parent_id": parent,
            "timestamp": ts, "content_model": "wikitext", "content_available": text is not None, "wikitext": text,
            "source": "synthetic", "text_sha256": hashlib.sha256(text.encode()).hexdigest() if text is not None else None}


def page(page_id: int = 1, title: str = "Test Article", wiki_id: str = "enwiki") -> dict:
    return {"kind": "page", "wiki_id": wiki_id, "page_id": page_id, "title": title, "source_host": "en.wikipedia.org", "ns": 0}


def window(start: str, end: str, preds: list[int] | None, status: str = "complete", page_id: int = 1,
           wiki_id: str = "enwiki", source: str = "synthetic") -> dict:
    return {"kind": "window", "wiki_id": wiki_id, "page_id": page_id, "coverage_start": start, "coverage_end": end,
            "status": status, "source": source, "predecessor_ids": preds, "revision_count": 0, "api_pages": 0, "error": None}


def article(lead: str, sections: dict[str, str] | None = None) -> str:
    out = [lead]
    for h, body in (sections or {}).items():
        out.append(f"\n== {h} ==\n{body}")
    out.append("\n== References ==\n{{reflist}}")
    return "\n".join(out)
