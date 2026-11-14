import json

from conftest import article, page, rev, window
from wtm import api as api_mod
from wtm.projection import build
from wtm.staging import load_records

SHARED = "The plant is located on the island of Olkiluoto on the coast of the Gulf of Bothnia in Eurajoki."
FUTURE = "Unit 3 entered regular commercial operation on 16 April 2023 after eighteen years of construction."
PAST = "Unit 3 is still under construction and commissioning tests continue; no start date is confirmed."


def seed(db, embedder):
    load_records(db, [
        page(),
        window("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z", [1]),
        window("2023-04-01T00:00:00Z", "2023-05-01T00:00:00Z", [2]),
        rev(1, "2019-12-01T00:00:00Z", article(SHARED, {"Unit 3": PAST})),
        rev(2, "2023-03-01T00:00:00Z", article(SHARED, {"Unit 3": PAST + " Testing was delayed again."})),
        rev(3, "2023-04-16T12:00:00Z", article(SHARED, {"Unit 3": FUTURE})),
    ])
    return build(db, embedder, log=lambda *_: None)


def ask(client, before, after, q="When did unit 3 enter commercial operation?"):
    r = client.post("/ask", json={"wiki_id": "enwiki", "page_id": 1, "before": before, "after": after, "question": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_future_passage_never_retrieved_or_cited_for_earlier_date(db, embedder, client):
    seed(db, embedder)
    calls = []

    def sneaky_llm(messages):
        calls.append(messages)
        # Tries to cite a snippet that would only exist on the later side.
        return json.dumps({"before": {"status": "answered", "statements": [{"text": "It entered operation in 2023.", "snippet_ids": ["B1"]}]},
                           "after": {"status": "insufficient_evidence", "statements": []}, "change": None})

    api_mod.STATE["llm"] = sneaky_llm
    out = ask(client, "2020-01-15", "2020-01-20")
    for side in ("before", "after"):
        assert out[side]["revision_id"] == 1
        assert out["evidence"][side], "earlier revision has evidence"
        assert all(FUTURE not in s["text"] for s in out["evidence"][side])
    assert FUTURE not in calls[0][1]["content"]
    assert out["generation"]["status"] == "model_output_rejected"  # B1 cited on the 'before' side


def test_shared_text_cites_each_sides_own_revision(db, embedder, client):
    seed(db, embedder)
    out = ask(client, "2020-01-15", "2023-04-20", q="Where is the plant located?")
    b, a = out["evidence"]["before"], out["evidence"]["after"]
    shared_b = [s for s in b if SHARED in s["text"]]
    shared_a = [s for s in a if SHARED in s["text"]]
    assert shared_b and shared_a
    assert shared_b[0]["chunk_id"] == shared_a[0]["chunk_id"]  # deduplicated chunk
    assert shared_b[0]["url"].endswith("oldid=1") and shared_a[0]["url"].endswith("oldid=3")
    assert all(s["id"].startswith("A") for s in b) and all(s["id"].startswith("B") for s in a)


def test_valid_model_answer_uses_db_excerpts(db, embedder, client):
    seed(db, embedder)

    def good_llm(messages):
        return json.dumps({"before": {"status": "answered", "statements": [{"text": "Still under construction.", "snippet_ids": ["A1"]}]},
                           "after": {"status": "answered", "statements": [{"text": "Commercial operation began 16 April 2023.", "snippet_ids": ["B1"]}]},
                           "change": {"text": "Moved from construction to operation.", "snippet_ids": ["A1", "B1"]}})

    api_mod.STATE["llm"] = good_llm
    out = ask(client, "2020-01-15", "2023-04-20")
    g = out["generation"]
    assert g["status"] == "ok"
    cb = g["answer"]["before"]["statements"][0]["citations"][0]
    ca = g["answer"]["after"]["statements"][0]["citations"][0]
    assert cb["url"].endswith("oldid=1") and ca["url"].endswith("oldid=3")
    assert cb["excerpt"] == out["evidence"]["before"][0]["text"]


def test_invalid_ids_malformed_output_and_outage(db, embedder, client):
    seed(db, embedder)
    for llm, expected in [
        (lambda m: json.dumps({"before": {"status": "answered", "statements": [{"text": "x", "snippet_ids": ["A99"]}]},
                               "after": {"status": "insufficient_evidence", "statements": []}, "change": None}), "model_output_rejected"),
        (lambda m: "Sure! Here is my answer without JSON", "model_output_rejected"),
        (lambda m: json.dumps({"before": {"status": "answered", "statements": [{"text": "uncited", "snippet_ids": []}]},
                               "after": {"status": "answered", "statements": []}}), "model_output_rejected"),
    ]:
        api_mod.STATE["llm"] = llm
        out = ask(client, "2020-01-15", "2023-04-20")
        assert out["generation"]["status"] == expected
        assert out["evidence"]["before"] and out["evidence"]["after"]  # fallback evidence still present

    def down(_):
        raise TimeoutError("model timeout")

    api_mod.STATE["llm"] = down
    out = ask(client, "2020-01-15", "2023-04-20")
    assert out["generation"]["status"] == "model_unavailable"
    assert out["evidence"]["after"]


def test_generation_disabled_still_returns_evidence(db, embedder, client):
    seed(db, embedder)
    out = ask(client, "2020-01-15", "2023-04-20")
    assert out["generation"]["status"] == "generation_disabled"
    assert any(FUTURE in s["text"] for s in out["evidence"]["after"])


def test_compare_and_validation(db, embedder, client):
    seed(db, embedder)
    r = client.get("/compare", params={"wiki_id": "enwiki", "page_id": 1, "before": "2023-04-20", "after": "2020-01-15"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "reversed_dates"
    r = client.get("/compare", params={"wiki_id": "enwiki", "page_id": 1, "before": "2020-01-15", "after": "2020-01-20"}).json()
    assert r["diff"]["identical"] and r["diff"]["same_revision"]
    r = client.get("/compare", params={"wiki_id": "enwiki", "page_id": 1, "before": "2020-01-15", "after": "2023-04-20"}).json()
    assert r["date_only_end_of_day"] == {"before": True, "after": True}
    assert r["before"]["requested_at"] == "2020-01-15T23:59:59.999999Z"
    assert not r["diff"]["identical"]
    changed = [p for s in r["diff"]["sections"] for p in s["paragraphs"] if p["status"] != "same"]
    assert changed
    # A side outside coverage renders independently.
    r = client.get("/compare", params={"wiki_id": "enwiki", "page_id": 1, "before": "2021-06-01", "after": "2023-04-20"}).json()
    assert r["before"]["status"] == "not_covered" and r["after"]["status"] == "ok" and r["diff"] is None
    r = client.get("/compare", params={"wiki_id": "enwiki", "page_id": 1, "before": "garbage", "after": "2023-04-20"})
    assert r.status_code == 400


def test_articles_and_timeline(db, embedder, client):
    seed(db, embedder)
    a = client.get("/articles").json()["articles"]
    assert a[0]["title"] == "Test Article" and len(a[0]["windows"]) == 2
    tl = client.get("/timeline", params={"wiki_id": "enwiki", "page_id": 1, "start": "2023-04-01T00:00:00Z",
                                         "end": "2023-05-01T00:00:00Z"}).json()["states"]
    assert [s["revision_id"] for s in tl] == [2, 3]
    assert tl[0]["source_timestamp"] == "2023-03-01T00:00:00Z" and tl[0]["start"] == "2023-04-01T00:00:00Z"
