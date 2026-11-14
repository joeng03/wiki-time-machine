"""Fetcher (mocked HTTP) and organizer parquet adapter tests -- no network."""
from datetime import datetime

import pytest

from wtm.fetch import ApiError, WikiApi, fetch_window


class FakeResp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


def mkrev(rid, ts, text="x", hidden=False):
    r = {"revid": rid, "parentid": rid - 1, "timestamp": ts, "slots": {"main": {"contentmodel": "wikitext"}}}
    if hidden:
        r["slots"]["main"]["texthidden"] = True
    else:
        r["slots"]["main"]["content"] = text
    return r


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.headers = {}
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        item = self.script.pop(0)
        return item(params) if callable(item) else item


def page_resp(revs, cont=None):
    d = {"query": {"pages": [{"pageid": 5, "revisions": revs}]}}
    if cont:
        d["continue"] = cont
    return FakeResp(200, d)


def api(session):
    return WikiApi("WTM-test/0 (test@example.invalid)", session=session, min_interval=0, sleep=lambda s: None)


def test_requires_contactable_user_agent():
    with pytest.raises(ApiError):
        WikiApi("python-requests")


def test_continuation_retry_predecessor_and_hidden_text():
    s = FakeSession([
        FakeResp(429, headers={"Retry-After": "1"}),
        page_resp([mkrev(10, "2023-04-01T00:00:00Z"), mkrev(11, "2023-04-02T00:00:00Z")], {"rvcontinue": "20230403|12", "continue": "||"}),
        page_resp([mkrev(12, "2023-04-03T00:00:00Z", hidden=True)]),
        # backward query: returns window-start revision too (must be deduped) and two same-second predecessors
        page_resp([mkrev(9, "2023-03-30T10:00:00Z"), mkrev(8, "2023-03-30T10:00:00Z"), mkrev(7, "2023-03-01T00:00:00Z")]),
    ])
    revs, w = fetch_window(api(s), "enwiki", 5, datetime(2023, 4, 1), datetime(2023, 4, 10))
    assert w["status"] == "complete" and w["api_pages"] == 3
    assert s.calls[2]["rvcontinue"] == "20230403|12" and s.calls[2]["continue"] == "||"  # full continuation object sent back
    assert s.calls[3]["rvdir"] == "older" and s.calls[3]["rvstart"] == "2023-03-31T23:59:59Z"
    assert w["predecessor_ids"] == [8, 9]
    assert [r["revision_id"] for r in revs] == [8, 9, 10, 11, 12]
    hidden = [r for r in revs if r["revision_id"] == 12][0]
    assert hidden["content_available"] is False and hidden["wikitext"] is None
    assert s.calls[1]["rvend"] == "2023-04-09T23:59:59Z"  # half-open end


def test_api_error_mid_pagination_marks_window_failed():
    s = FakeSession([
        page_resp([mkrev(10, "2023-04-01T00:00:00Z")], {"rvcontinue": "x", "continue": "||"}),
        FakeResp(500),
    ])
    revs, w = fetch_window(api(s), "enwiki", 5, datetime(2023, 4, 1), datetime(2023, 4, 10))
    assert w["status"] == "failed" and "HTTP 500" in w["error"]
    assert [r["revision_id"] for r in revs] == [10]


def test_page_cap_marks_window_failed():
    s = FakeSession([page_resp([mkrev(10, "2023-04-01T00:00:00Z")], {"rvcontinue": "x", "continue": "||"})] * 3)
    _, w = fetch_window(api(s), "enwiki", 5, datetime(2023, 4, 1), datetime(2023, 4, 10), max_api_pages=2)
    assert w["status"] == "failed" and "cap" in w["error"]


def test_parquet_m_adapter(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from wtm.datasets import DatasetError, m_versions_records

    t = pa.table({
        "page_id": [1, 1, 2, 2], "title": ["A", "A", "B", "B"], "rev_id": [10, 11, 20, 22], "parent_id": [0, 10, 0, 21],
        "timestamp": pa.array([datetime(2010, 1, 1), datetime(2011, 1, 1), datetime(2012, 1, 1), datetime(2013, 1, 1)],
                              type=pa.timestamp("us", tz="UTC")),
        "user": ["secret-a", "secret-b", "c", "d"], "text": ["a1", "a2", "b1", "b2"],
    })
    p = tmp_path / "versions-01.parquet"
    pq.write_table(t, p)
    recs = list(m_versions_records(str(p), {1, 2}))
    assert not any("secret" in str(r) for r in recs)  # editor identities never read
    wins = {r["page_id"]: r for r in recs if r["kind"] == "window"}
    assert wins[1]["status"] == "complete" and wins[1]["coverage_start"] == "2010-01-01T00:00:00Z"
    assert wins[2]["status"] == "failed"  # parent 21 missing -> incomplete history, never published
    with pytest.raises(DatasetError):
        list(m_versions_records(str(p), {3}))
    pq.write_table(t.drop(["text"]), p)
    with pytest.raises(DatasetError):
        list(m_versions_records(str(p), {1}))
