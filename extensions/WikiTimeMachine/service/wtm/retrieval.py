"""MariaDB-native vector retrieval.

Article/time-scoped retrieval is *correctness-first exact ranking over the eligible set*: chunks are
filtered by membership in the exact resolved revision (join on wiki_revision_chunk) and ranked with
VEC_DISTANCE_COSINE in SQL. A global ANN top-K followed by date filtering could miss every relevant
historical passage, so it is never used for answers.

The HNSW VECTOR INDEX is used (and measured) by corpus-wide topic search, where the query is a bare
`ORDER BY VEC_DISTANCE_COSINE(...) LIMIT k` that the optimizer can serve from the index.
"""
from __future__ import annotations

import numpy as np

from .embed import to_sql_vector
from .normalize import PARSER_VERSION

REVISION_TOPK_SQL = """
SELECT rc.ordinal, rc.section_heading, c.chunk_id, c.text,
       VEC_DISTANCE_COSINE(c.embedding, VEC_FromText(%s)) AS distance
FROM wiki_revision_chunk rc
JOIN wiki_chunk c ON c.chunk_id = rc.chunk_id
WHERE rc.wiki_id = %s AND rc.revision_id = %s
  AND rc.parser_version = %s AND rc.embed_model = %s
ORDER BY distance
LIMIT %s
""".strip()

# Candidate ANN form with the eligibility predicate in SQL (measured by `wtm bench`; see docs).
REVISION_ANN_SQL = """
SELECT c.chunk_id, VEC_DISTANCE_COSINE(c.embedding, VEC_FromText(%s)) AS distance
FROM wiki_chunk c
WHERE c.chunk_id IN (SELECT rc.chunk_id FROM wiki_revision_chunk rc WHERE rc.wiki_id = %s AND rc.revision_id = %s)
ORDER BY VEC_DISTANCE_COSINE(c.embedding, VEC_FromText(%s))
LIMIT %s
""".strip()

CORPUS_ANN_SQL = """
SELECT passage_id, title, page_id, lang, ordinal, text
FROM corpus_passage
ORDER BY VEC_DISTANCE_COSINE(embedding, VEC_FromText(%s))
LIMIT %s
""".strip()

CORPUS_EXACT_SQL = """
SELECT passage_id FROM corpus_passage IGNORE INDEX (idx_corpus_embedding)
ORDER BY VEC_DISTANCE_COSINE(embedding, VEC_FromText(%s))
LIMIT %s
""".strip()


def revision_evidence(conn, wiki_id: str, revision_id: int, qvec: np.ndarray, embed_model: str, k: int = 5,
                      prefix: str = "S", url: str = "") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(REVISION_TOPK_SQL, (to_sql_vector(qvec), wiki_id, revision_id, PARSER_VERSION, embed_model, k))
        rows = cur.fetchall()
    return [{"id": f"{prefix}{i + 1}", "revision_id": revision_id, "url": url, "section": r["section_heading"],
             "ordinal": r["ordinal"], "chunk_id": r["chunk_id"], "text": r["text"],
             "distance": round(float(r["distance"]), 4)} for i, r in enumerate(rows)]


def corpus_search(conn, qvec: np.ndarray, k: int = 10) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(CORPUS_ANN_SQL, (to_sql_vector(qvec), k))
        rows = cur.fetchall()
    for r in rows:
        r["url"] = f"https://{r['lang']}.wikipedia.org/?curid={r['page_id']}"
        r["text"] = r["text"][:600]
    return rows


def explain(conn, sql: str, params: tuple) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("EXPLAIN " + sql, params)
        return cur.fetchall()
