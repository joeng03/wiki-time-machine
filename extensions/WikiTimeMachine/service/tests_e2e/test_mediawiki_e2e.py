"""End-to-end through local MediaWiki (same-origin API modules) on the cached REAL demo fixtures.

Run:  docker compose exec service pytest tests_e2e   (requires `make demo` to have loaded fixtures/real)
"""
import os
import re

import pytest
import requests

from wtm.db import connect
from wtm.snapshots import revision_sections

MW = os.environ.get("WTM_MW_URL", "http://mediawiki")
OLKILUOTO = 1977274
DEMO = {"page": OLKILUOTO, "before": "2021-12-15", "after": "2023-04-20"}


def api(**params):
    r = requests.get(f"{MW}/api.php", params={**params, "format": "json", "formatversion": "2"}, timeout=60)
    r.raise_for_status()
    return r.json()


@pytest.fixture(scope="module")
def db():
    c = connect("reader")
    yield c
    c.close()


def test_articles_lists_real_pages():
    titles = {a["title"] for a in api(action="wtmarticles")["wtmarticles"]["articles"]}
    assert {"Olkiluoto Nuclear Power Plant", "Finland–NATO relations", "Helsinki Central Library Oodi"} <= titles


def test_compare_resolves_stored_revisions_with_permanent_links(db):
    d = api(action="wtmcompare", **DEMO)["wtmcompare"]
    with db.cursor() as cur:
        for side in ("before", "after"):
            s = d[side]
            assert s["status"] == "ok"
            assert s["url"] == f"https://en.wikipedia.org/w/index.php?oldid={s['revision_id']}"
            cur.execute("SELECT revision_ts, source FROM wiki_revision WHERE wiki_id='enwiki' AND revision_id=%s", (s["revision_id"],))
            row = cur.fetchone()
            assert row["source"] == "api"
            assert row["revision_ts"].strftime("%Y-%m-%dT%H:%M:%SZ") == s["source_timestamp"]
            # independent predecessor lookup over the archive agrees with the SVT answer
            cur.execute("SELECT revision_id FROM wiki_revision WHERE wiki_id='enwiki' AND page_id=%s AND revision_ts <= %s "
                        "ORDER BY revision_ts DESC, revision_id DESC LIMIT 1", (OLKILUOTO, s["requested_at"].replace("T", " ").rstrip("Z")))
            assert cur.fetchone()["revision_id"] == s["revision_id"]
    assert d["before"]["revision_id"] == 1059771803 and d["after"]["revision_id"] == 1150938986
    assert not d["diff"]["identical"] and d["diff"]["stats"]["paragraphs_changed"] > 0


def test_ask_excerpts_come_from_each_sides_own_revision(db):
    d = api(action="wtmask", question="How did the article describe the status of Olkiluoto 3 at these two dates?", **DEMO)["wtmask"]
    with db.cursor() as cur:
        for side in ("before", "after"):
            rid = d[side]["revision_id"]
            prose = "\n".join(p for s in revision_sections(db, "enwiki", rid) for p in s["paragraphs"])
            cur.execute("SELECT wikitext FROM wiki_revision WHERE wiki_id='enwiki' AND revision_id=%s", (rid,))
            wikitext = cur.fetchone()["wikitext"]
            assert d["evidence"][side]
            for ev in d["evidence"][side]:
                assert ev["revision_id"] == rid and ev["url"].endswith(f"oldid={rid}")
                cur.execute("SELECT 1 FROM wiki_revision_chunk WHERE wiki_id='enwiki' AND revision_id=%s AND chunk_id=%s", (rid, ev["chunk_id"]))
                assert cur.fetchone(), "cited chunk belongs to that side's revision"
                for para in ev["text"].split("\n"):
                    assert para in prose, "excerpt agrees with the stored revision's extracted text"
                words = [w for w in re.findall(r"[A-Za-z]{7,}", ev["text"])][:10]
                assert all(w in wikitext for w in words), "excerpt words exist in the raw source wikitext"


def test_validation_errors_and_special_page():
    e = api(action="wtmcompare", page=OLKILUOTO, before="2023-04-20", after="2021-12-15")
    assert e["error"]["code"] == "wtm-reversed_dates"
    e = api(action="wtmcompare", page=OLKILUOTO, before="<script>", after="2021-12-15")
    assert e["error"]["code"] == "wtm-invalid_time"
    e = api(action="wtmsnapshot", page=OLKILUOTO, at="2020-06-01")
    assert e["wtmsnapshot"]["snapshot"]["status"] == "not_covered"
    html = requests.get(f"{MW}/index.php", params={"title": "Special:WikiTimeMachine", "q": "<img src=x onerror=alert(1)>"}, timeout=30).text
    assert 'id="wtm-form"' in html and "<img src=x" not in html
