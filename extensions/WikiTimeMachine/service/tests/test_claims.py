import json
from datetime import date, datetime

import pytest

from conftest import page, rev, window
from wtm.claims import (INFOBOX_EXTRACTOR, MAX_DATE, PDate, active_claim_build, claim_history, claims_as_of,
                        extract_for_build, extract_infobox, llm_extractor_id, make_claim, parse_date_text, project_claims,
                        validate_llm_claims, office_aliases)
from wtm.projection import build
from wtm.snapshots import pinned_build, resolve
from wtm.staging import load_records

TITLE = "Prime Minister of Finland"


def pm_rev(incumbent, since, extra=""):
    return ("{{Infobox official post\n| post = Prime Minister\n| incumbent = [[" + incumbent + "]]\n| incumbentsince = "
            + since + "\n| formation = {{Start date and age|1917|11|27|df=yes}}\n| inaugural = [[Pehr Evind Svinhufvud]]\n}}\n"
            "The '''prime minister of Finland''' is the leader of the [[Finnish Government]]. Finland's first prime minister was "
            "Pehr Evind Svinhufvud, who was appointed to the post on 27 November 1917.\n" + extra + "\n== References ==\n{{reflist}}")


MARIN_ENDED = "Sanna Marin served as prime minister from 10 December 2019 to 20 June 2023."


def seed(db, embedder):
    load_records(db, [
        page(7, TITLE),
        window("2019-12-01T00:00:00Z", "2019-12-20T00:00:00Z", [1], page_id=7),
        window("2023-06-01T00:00:00Z", "2023-07-01T00:00:00Z", [2], page_id=7),
        rev(1, "2019-11-12T00:00:00Z", pm_rev("Antti Rinne", "6 June 2019"), page_id=7),
        rev(2, "2019-12-10T12:00:00Z", pm_rev("Sanna Marin", "10 December 2019"), page_id=7),
        rev(3, "2023-06-20T11:56:00Z", pm_rev("Petteri Orpo", "20 June 2023", "\n== History ==\n" + MARIN_ENDED), page_id=7),
    ])
    return build(db, embedder, log=lambda *_: None)["build_id"]


def holders(db, build_id, as_of, valid_at):
    cb = active_claim_build(db, build_id)
    t = datetime.fromisoformat(as_of)
    snap = resolve(db, build_id, "enwiki", 7, t)
    rows = claims_as_of(db, build_id, cb, "enwiki", 7, t, date.fromisoformat(valid_at), snap.get("revision_id"), snap.get("url"))
    return rows


def test_dates_and_precision():
    assert parse_date_text("6 June 2019") == PDate(date(2019, 6, 6), "day")
    assert parse_date_text("June 6, 2019") == PDate(date(2019, 6, 6), "day")
    assert parse_date_text("March 2012") == PDate(date(2012, 3, 1), "month")
    assert parse_date_text("since 2012") == PDate(date(2012, 1, 1), "year")
    assert parse_date_text("") == PDate(None, "unknown")
    c = make_claim("X", "Y", PDate(date(2012, 1, 1), "year"), PDate(date(2018, 1, 1), "year"), "ended")
    assert (c.valid_from, c.valid_to, c.from_precision, c.to_precision) == (date(2012, 1, 1), date(2019, 1, 1), "year", "year")
    c = make_claim("X", "Y", PDate(None, "unknown"), PDate(None, "unknown"), "ongoing")
    assert c.valid_from == date(1, 1, 1) and c.valid_to == MAX_DATE and c.from_precision == "unknown"
    assert make_claim("X", "Y", PDate(None, "unknown"), PDate(None, "unknown"), "unknown") is None
    c = make_claim("X", "Y", PDate(date(2012, 1, 1), "year"), PDate(None, "unknown"), "unknown")
    assert (c.valid_to, c.to_precision) == (date(2013, 1, 1), "unknown")
    assert make_claim("X", "Y", PDate(date(2020, 1, 1), "day"), PDate(date(2019, 1, 1), "day"), "ended") is None


def test_infobox_extraction():
    claims = extract_infobox(pm_rev("Sanna Marin", "10 December 2019"), TITLE)
    by = {c.holder: (c, e) for c, e in claims}
    marin, ex = by["Sanna Marin"]
    assert (marin.office, marin.valid_from, marin.valid_to, marin.end_status) == (TITLE, date(2019, 12, 10), MAX_DATE, "ongoing")
    assert "incumbentsince = 10 December 2019" in ex
    first = by["Pehr Evind Svinhufvud"][0]
    assert (first.valid_from, first.valid_to, first.end_status, first.to_precision) == (
        date(1917, 11, 27), date(1917, 11, 28), "unknown", "unknown")
    bio = "{{Infobox officeholder\n| office = Prime Minister of Finland\n| term_start = 10 December 2019\n| term_end = 20 June 2023\n}}"
    (c, _), = extract_infobox(bio, "Sanna Marin")
    assert (c.holder, c.valid_to, c.end_status) == ("Sanna Marin", date(2023, 6, 20), "ended")


def test_llm_claim_validation():
    aliases = office_aliases(TITLE, "Prime Minister")
    raw = {"claims": [
        {"holder": "Sanna Marin", "office": "Prime Minister of Finland", "start": "2019-12-10", "end": "2023-06-20",
         "end_status": "ended", "excerpt": MARIN_ENDED},
        {"holder": "Sanna Marin", "office": "prime minister", "start": "2019", "end": None, "end_status": "ongoing",
         "excerpt": "Sanna Marin was the youngest prime minister."},                           # not verbatim
        {"holder": "Annika Saarikko", "office": "Minister of Finance", "start": "2021", "end": None,
         "end_status": "unknown", "excerpt": MARIN_ENDED},                                       # holder/office wrong
        {"holder": "Sanna Marin", "office": "prime minister", "start": "2018-01-05", "end": None,
         "end_status": "ongoing", "excerpt": MARIN_ENDED},                                       # date not in excerpt
    ]}
    acc, rej = validate_llm_claims(raw, "History: " + MARIN_ENDED + " More text.", aliases, TITLE)
    assert len(acc) == 1 and len(rej) == 3
    c = acc[0][0]
    assert (c.valid_from, c.valid_to, c.end_status, c.to_precision) == (date(2019, 12, 10), date(2023, 6, 20), "ended", "day")


def test_bitemporal_infobox_only(db, embedder):
    b = seed(db, embedder)
    extract_for_build(db, b, [("enwiki", 7)], log=lambda *_: None)
    res = project_claims(db, b, [INFOBOX_EXTRACTOR], log=lambda *_: None)
    assert res["validation"]["ok"]
    # As of Wikipedia on 5 Dec 2019, who did the article say held office on 15 Dec 2019?  -> Rinne (ongoing)
    assert [r["holder"] for r in holders(db, b, "2019-12-05T00:00:00", "2019-12-15")] == ["Antti Rinne"]
    # As of Wikipedia on 15 Dec 2019 -> Marin
    rows = holders(db, b, "2019-12-15T00:00:00", "2019-12-15")
    assert [r["holder"] for r in rows] == ["Sanna Marin"]
    assert rows[0]["sources"][0]["url"].endswith("oldid=2") and "10 December 2019" in rows[0]["sources"][0]["excerpt"]
    assert rows[0]["display"] == "2019-12-10 → present"
    assert [r["holder"] for r in holders(db, b, "2023-06-25T00:00:00", "2023-06-25")] == ["Petteri Orpo"]
    # inaugural claim: start known, end unknown
    first = holders(db, b, "2023-06-25T00:00:00", "1917-11-27")
    assert [(r["holder"], r["end_status"], r["display"]) for r in first] == [("Pehr Evind Svinhufvud", "unknown", "1917-11-27 → end unknown")]
    assert holders(db, b, "2023-06-25T00:00:00", "1918-01-01") == []  # unknown end is never extended
    # outside system-time coverage -> nothing asserted
    assert holders(db, b, "2021-01-01T00:00:00", "2021-01-01") == []
    # rows are closed history
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_claim_snapshot")
        assert cur.fetchone()["n"] == 0
    assert project_claims(db, b, [INFOBOX_EXTRACTOR], log=lambda *_: None)["reused"]


def test_timeline_claims_use_published_extractors_and_each_revisions_evidence(db, embedder, client):
    b = seed(db, embedder)
    extract_for_build(db, b, [("enwiki", 7)], log=lambda *_: None)
    project_claims(db, b, [INFOBOX_EXTRACTOR], log=lambda *_: None)
    # A staged extractor's source must not leak into the published timeline.
    with db.cursor() as cur:
        cur.execute("INSERT INTO wiki_revision_claim (wiki_id,revision_id,claim_id,extractor,source_ref,excerpt) "
                    "SELECT wiki_id,revision_id,claim_id,'unpublished','staged','not published' "
                    "FROM wiki_revision_claim LIMIT 1")
    data = client.get("/timeline", params={"wiki_id": "enwiki", "page_id": 7,
                     "start": "2019-12-01T00:00:00Z", "end": "2023-07-01T00:00:00Z"}).json()
    assert data["claim_build_id"] == active_claim_build(db, b)
    states = data["states"]
    assert [s["revision_id"] for s in states] == [1, 2, 2, 3]
    assert any(c["holder"] == "Antti Rinne" for c in states[0]["claims"])
    assert any(c["holder"] == "Sanna Marin" for c in states[1]["claims"])
    assert any(c["holder"] == "Petteri Orpo" for c in states[-1]["claims"])
    assert not any(c["holder"] == "Petteri Orpo" for s in states[:-1] for c in s["claims"])
    assert states[1]["end"] != states[2]["start"]  # The unobserved years remain a gap.
    for s in states:
        for c in s["claims"]:
            assert c["sources"]
            for source in c["sources"]:
                assert source["revision_id"] == s["revision_id"]
                assert source["url"] == s["url"]
                assert source["extractor"] == INFOBOX_EXTRACTOR
                assert source["excerpt"] != 'not published'


def test_bitemporal_with_llm_extractor(db, embedder, client):
    b = seed(db, embedder)
    calls = []

    def fake_llm(messages):
        calls.append(messages[1]["content"])
        if "served as prime minister" in messages[1]["content"]:
            return json.dumps({"claims": [{"holder": "Sanna Marin", "office": "Prime Minister of Finland", "start": "2019-12-10",
                                           "end": "2023-06-20", "end_status": "ended", "excerpt": MARIN_ENDED},
                                          {"holder": "Sanna Marin", "office": "Prime Minister", "start": "2020", "end": None,
                                           "end_status": "ongoing", "excerpt": "invented sentence"}]})
        return json.dumps({"claims": []})

    stats = extract_for_build(db, b, [("enwiki", 7)], llm=fake_llm, llm_model="fake", log=lambda *_: None)
    assert stats["llm_accepted"] == 1 and stats["llm_rejected"] == 1
    n_calls = len(calls)
    assert n_calls == stats["llm_chunks_called"]
    stats2 = extract_for_build(db, b, [("enwiki", 7)], llm=fake_llm, llm_model="fake", log=lambda *_: None)
    assert len(calls) == n_calls and stats2["llm_chunks_cached"] == n_calls  # cached per unique chunk
    project_claims(db, b, [INFOBOX_EXTRACTOR, llm_extractor_id("fake")], log=lambda *_: None)
    # As of Wikipedia 25 Jun 2023, who held office on 1 Jan 2021? -> Marin, ended claim, prose excerpt from oldid=3
    rows = holders(db, b, "2023-06-25T00:00:00", "2021-01-01")
    assert [(r["holder"], r["end_status"]) for r in rows] == [("Sanna Marin", "ended")]
    src = rows[0]["sources"][0]
    assert src["excerpt"] == MARIN_ENDED and src["url"].endswith("oldid=3") and src["extractor"] == "llm:fake:tenure-v1"
    # As of Wikipedia 15 Dec 2019, same valid date -> Marin, but as an ongoing (open-ended) claim
    rows = holders(db, b, "2019-12-15T00:00:00", "2021-01-01")
    assert [(r["holder"], r["end_status"]) for r in rows] == [("Sanna Marin", "ongoing")]
    hist = claim_history(db, active_claim_build(db, b), "enwiki", 7, date(2021, 1, 1))
    # What Wikipedia asserted about 1 Jan 2021, across system time (two coverage windows):
    assert [(h["holder"], h["end_status"], h["asserted_from"], h["asserted_until"]) for h in hist] == [
        ("Antti Rinne", "ongoing", "2019-12-01T00:00:00Z", "2019-12-10T12:00:00Z"),   # incumbent since June 2019, open-ended
        ("Sanna Marin", "ongoing", "2019-12-10T12:00:00Z", "2019-12-20T00:00:00Z"),
        ("Sanna Marin", "ongoing", "2023-06-01T00:00:00Z", "2023-06-20T11:56:00Z"),
        ("Sanna Marin", "ended", "2023-06-20T11:56:00Z", "2023-07-01T00:00:00Z")]
    # through the HTTP API
    r = client.get("/claims", params={"wiki_id": "enwiki", "page_id": 7, "before": "2019-12-15", "after": "2023-06-25",
                                      "valid_at": "2023-06-25"}).json()
    assert [c["holder"] for c in r["sides"]["before"]["claims"]] == ["Sanna Marin"]
    assert [c["holder"] for c in r["sides"]["after"]["claims"]] == ["Petteri Orpo"]
    assert client.get("/claims", params={"wiki_id": "enwiki", "page_id": 7, "before": "2019-12-15", "after": "2023-06-25",
                                         "valid_at": "June 2023"}).status_code == 400
