"""The committed synthetic capture file loads through the same path as real captures."""
import os

from wtm.fetch import read_capture
from wtm.projection import build
from wtm.snapshots import pinned_build, resolve
from wtm.staging import load_records
from wtm.timeutil import parse_utc

FIXTURE = os.path.join(os.environ.get("WTM_FIXTURES", "/app/fixtures"), "synthetic", "temporal-isolation.jsonl")


def test_synthetic_capture_file(db, embedder, client):
    load_records(db, read_capture(FIXTURE))
    build(db, embedder, log=lambda *_: None)
    b = pinned_build(db)
    at = lambda s: resolve(db, b, "enwiki", 1, parse_utc(s, date_only_end_of_day=False)[0])
    assert at("2023-04-16T12:00:00Z")["revision_id"] == 4          # tie -> greatest id
    assert at("2023-04-25T00:00:00Z")["status"] == "unavailable"
    assert at("2023-06-15T00:00:00Z")["status"] == "not_covered"   # failed window never published
    out = client.post("/ask", json={"wiki_id": "enwiki", "page_id": 1, "before": "2020-01-10", "after": "2023-04-10",
                                    "question": "When did unit 3 enter commercial operation?"}).json()
    assert all("16 April 2023" not in s["text"] for side in ("before", "after") for s in out["evidence"][side])
