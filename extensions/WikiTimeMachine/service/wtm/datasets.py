"""Streaming adapters for the organizer datasets (github.com/MariaDB/mariadb-wikipedia-datasets).

- M ("every version", Finnish Wikipedia): versions-NN.parquet with columns
  page_id, title, rev_id, parent_id, timestamp (UTC), user, anonymous, comment, minor, sha1, bytes, text.
  Editor identity/comment columns are never read. Full history per page up to the dump date, so one
  coverage window [first revision, dump date) per page -- if the parent chain is complete.
- S ("now", English articles): articles-en.parquet with lang, page_id, rev_id, title, url, text (Markdown).
  Current snapshots only: used for the corpus-wide vector search demo, NOT for revision history.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Iterator

import numpy as np

from .embed import Embedder, to_sql_vector

M_REQUIRED = {"page_id", "title", "rev_id", "parent_id", "timestamp", "text"}
M_DUMP_DATE = datetime(2026, 10, 1)
S_REQUIRED = {"lang", "page_id", "title", "text"}


class DatasetError(ValueError):
    pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_provenance(path: str, sums_path: str | None) -> dict:
    digest = sha256_file(path)
    out = {"file": path.rsplit("/", 1)[-1], "sha256": digest, "verified": None}
    if sums_path:
        expected = {}
        for line in open(sums_path, encoding="utf-8"):
            parts = line.split()
            if len(parts) == 2:
                expected[parts[1].lstrip("*").rsplit("/", 1)[-1]] = parts[0]
        if out["file"] not in expected:
            raise DatasetError(f"{out['file']} not listed in {sums_path}")
        if expected[out["file"]] != digest:
            raise DatasetError(f"sha256 mismatch for {out['file']}")
        out["verified"] = True
    return out


def _to_naive_utc(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
    s = str(ts).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def m_versions_records(path: str, page_ids: set[int], wiki_id: str = "fiwiki", host: str = "fi.wikipedia.org",
                       dump_date: datetime = M_DUMP_DATE, sums_path: str | None = None,
                       batch_size: int = 256) -> Iterator[dict]:
    """Yield capture records (manifest/page/revision/window) for the selected pages of an M file."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    cols = set(pf.schema_arrow.names)
    missing = M_REQUIRED - cols
    if missing:
        raise DatasetError(f"{path}: missing columns {sorted(missing)}")
    prov = verify_provenance(path, sums_path)
    yield {"kind": "manifest", "format": "wtm-capture/1", "source": {"dataset": "mariadb-wikipedia-datasets M", **prov},
           "wiki_id": wiki_id, "editor_identities": "omitted",
           "attribution": {"text": "Finnish Wikipedia contributors, CC BY-SA 4.0",
                           "license": "https://creativecommons.org/licenses/by-sa/4.0/"}}
    seen: dict[int, dict] = {}
    revs_by_page: dict[int, list[tuple]] = {}
    for batch in pf.iter_batches(batch_size=batch_size, columns=sorted(M_REQUIRED)):
        d = batch.to_pydict()
        for i, pid in enumerate(d["page_id"]):
            pid = int(pid)
            if pid not in page_ids:
                continue
            if pid not in seen:
                seen[pid] = {"kind": "page", "wiki_id": wiki_id, "page_id": pid, "title": d["title"][i],
                             "source_host": host, "ns": 0}
                yield seen[pid]
            text = d["text"][i]
            ts = _to_naive_utc(d["timestamp"][i])
            if ts >= dump_date:
                raise DatasetError(f"revision {d['rev_id'][i]} is after the declared dump date")
            rid, parent = int(d["rev_id"][i]), int(d["parent_id"][i] or 0) or None
            revs_by_page.setdefault(pid, []).append((ts, rid, parent))
            yield {"kind": "revision", "wiki_id": wiki_id, "page_id": pid, "revision_id": rid, "parent_id": parent,
                   "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "content_model": "wikitext",
                   "content_available": text is not None, "wikitext": text, "source": "parquet",
                   "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None}
    for pid, revs in revs_by_page.items():
        ids = {r[1] for r in revs}
        broken = [r[1] for r in revs if r[2] and r[2] not in ids]
        first = min(revs)
        yield {"kind": "window", "wiki_id": wiki_id, "page_id": pid,
               "coverage_start": first[0].strftime("%Y-%m-%dT%H:%M:%SZ"),
               "coverage_end": dump_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "status": "failed" if broken else "complete", "source": "parquet-m",
               "predecessor_ids": [], "revision_count": len(revs), "api_pages": 0,
               "error": f"parent chain incomplete: {broken[:5]}" if broken else None}
    for pid in page_ids - set(seen):
        raise DatasetError(f"page {pid} not found in {path}")


_MD_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def markdown_passages(text: str, max_words: int = 240) -> list[str]:
    paras, out, cur = [], [], []
    for block in re.split(r"\n\s*\n", text or ""):
        b = block.strip()
        if not b or _MD_HEADING.match(b) and len(b.split("\n")) == 1:
            continue
        paras.append(re.sub(r"\s+", " ", b))
    for p in paras:
        if cur and len(" ".join(cur + [p]).split()) > max_words:
            out.append(" ".join(cur))
            cur = []
        words = p.split()
        while len(words) > max_words:
            out.append(" ".join(words[:max_words]))
            words = words[max_words:]
        cur.append(" ".join(words))
    if cur:
        out.append(" ".join(cur))
    return out


def load_corpus(conn, path: str, embedder: Embedder, dataset: str = "mariadb-s-2026-10", limit_articles: int | None = None,
                max_passages: int = 6, log=print) -> dict:
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    missing = S_REQUIRED - set(pf.schema_arrow.names)
    if missing:
        raise DatasetError(f"{path}: missing columns {sorted(missing)}")
    cols = sorted(S_REQUIRED | ({"rev_id"} & set(pf.schema_arrow.names)))
    n_articles = n_passages = 0
    pending: list[tuple] = []

    def flush():
        nonlocal pending, n_passages
        if not pending:
            return
        vecs = embedder.embed([p[5] for p in pending])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT IGNORE INTO corpus_passage (dataset, lang, page_id, rev_id, title, ordinal, text, embed_model, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,VEC_FromText(%s))",
                [(dataset, p[0], p[1], p[2], p[3], p[4], p[5], embedder.model_id, to_sql_vector(v)) for p, v in zip(pending, vecs)])
        n_passages += len(pending)
        pending = []

    for batch in pf.iter_batches(batch_size=128, columns=cols):
        d = batch.to_pydict()
        for i in range(len(d["page_id"])):
            if limit_articles and n_articles >= limit_articles:
                break
            n_articles += 1
            title = d["title"][i]
            for o, passage in enumerate(markdown_passages(d["text"][i])[:max_passages]):
                pending.append((d["lang"][i], int(d["page_id"][i]), d.get("rev_id", [None] * len(d["page_id"]))[i],
                                title, o, f"{title}: {passage}"))
            if len(pending) >= 256:
                flush()
                log(f"  corpus: {n_articles} articles, {n_passages} passages")
        if limit_articles and n_articles >= limit_articles:
            break
    flush()
    return {"articles": n_articles, "passages": n_passages, "model": embedder.model_id}


def as_float_array(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float32)
