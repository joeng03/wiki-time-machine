from datetime import datetime

import pytest

from conftest import article, page, rev, window
from wtm.projection import BuildError, Interval, Rev, build, compute_intervals, merge_windows
from wtm.snapshots import pinned_build, resolve
from wtm.staging import ConflictError, load_records
from wtm.timeutil import parse_utc

D = datetime


def T(s):
    return parse_utc(s, date_only_end_of_day=False)[0]


def base_records():
    return [
        page(),
        window("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z", [1]),
        rev(1, "2019-12-15T10:00:00Z", article("Version one of the article.")),
        rev(2, "2020-01-10T12:00:00Z", article("Version two of the article.")),
        rev(3, "2020-01-20T08:00:00Z", article("Version three, lower id at the same second.")),
        rev(4, "2020-01-20T08:00:00Z", article("Version four, higher id at the same second.")),
        rev(5, "2020-01-25T00:00:00Z", None),  # hidden text
    ]


def status_rev(conn, at, mode="svt"):
    r = resolve(conn, pinned_build(conn), "enwiki", 1, T(at), mode)
    return r["status"], r.get("revision_id")


def test_compute_intervals_ties_holes_and_bounds():
    revs = [Rev(10, D(2020, 1, 5), True), Rev(12, D(2020, 1, 9), True), Rev(11, D(2020, 1, 9), True)]
    iv = compute_intervals(revs, D(2020, 1, 1), D(2020, 1, 31))
    # hole before creation, tie resolved to greatest id, closed at coverage end
    assert iv == [Interval(D(2020, 1, 5), D(2020, 1, 9), 10, True), Interval(D(2020, 1, 9), D(2020, 1, 31), 12, True)]


def test_merge_windows():
    assert merge_windows([(D(2020, 1, 10), D(2020, 2, 1)), (D(2020, 1, 1), D(2020, 1, 15)), (D(2020, 3, 1), D(2020, 4, 1))]) == \
        [(D(2020, 1, 1), D(2020, 2, 1)), (D(2020, 3, 1), D(2020, 4, 1))]


@pytest.mark.parametrize("mode", ["svt", "archive"])
def test_edit_boundaries_ties_unavailable_and_coverage(db, embedder, mode):
    load_records(db, base_records())
    build(db, embedder, log=lambda *_: None)
    assert status_rev(db, "2019-12-31T23:59:59.999999Z", mode) == ("not_covered", None)
    assert status_rev(db, "2020-01-01T00:00:00Z", mode) == ("ok", 1)              # predecessor at window start
    assert status_rev(db, "2020-01-10T11:59:59.999999Z", mode) == ("ok", 1)       # just before the edit
    assert status_rev(db, "2020-01-10T12:00:00Z", mode) == ("ok", 2)              # exactly at the edit
    assert status_rev(db, "2020-01-10T12:00:01Z", mode) == ("ok", 2)              # after the edit
    assert status_rev(db, "2020-01-20T08:00:00Z", mode) == ("ok", 4)              # same-second tie -> greatest id
    assert status_rev(db, "2020-01-25T00:00:01Z", mode) == ("unavailable", 5)
    assert status_rev(db, "2020-01-31T23:59:59.999999Z", mode) == ("unavailable", 5)
    assert status_rev(db, "2020-02-01T00:00:00Z", mode) == ("not_covered", None)  # coverage expired


def test_svt_rows_are_closed_history(db, embedder):
    load_records(db, base_records())
    res = build(db, embedder, log=lambda *_: None)
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_snapshot")  # current rows
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT revision_id, row_start, row_end FROM wiki_snapshot FOR SYSTEM_TIME ALL ORDER BY row_start")
        rows = cur.fetchall()
        assert [r["revision_id"] for r in rows] == [1, 2, 4, 5]
        assert rows[0]["row_start"] == D(2020, 1, 1) and rows[1]["row_start"] == D(2020, 1, 10, 12)
        assert rows[-1]["row_end"] == D(2020, 2, 1)
        # Direct AS OF after coverage end returns nothing (no inferred open-ended row).
        cur.execute("SELECT * FROM wiki_snapshot FOR SYSTEM_TIME AS OF CAST('2020-02-01 00:00:00' AS DATETIME(6)) WHERE build_id=%s",
                    (res["build_id"],))
        assert len(cur.fetchall()) == 0
    assert res["validation"]["ok"]


def test_reader_cannot_write_history(db, reader):
    import pymysql
    with pytest.raises(pymysql.MySQLError):
        with reader.cursor() as cur:
            cur.execute("SET SESSION system_versioning_insert_history = ON")
            cur.execute("INSERT INTO wiki_snapshot (build_id, wiki_id, page_id, revision_id, content_available, row_start, row_end) "
                        "VALUES (9,'x',1,1,1,'2020-01-01','2020-01-02')")


def test_duplicate_import_is_idempotent_and_build_reused(db, embedder):
    s1 = load_records(db, base_records())
    r1 = build(db, embedder, log=lambda *_: None)
    s2 = load_records(db, base_records())
    assert s2["revisions_new"] == 0 and s2["revisions_identical"] == 5 and s2["windows_existing"] == 1
    r2 = build(db, embedder, log=lambda *_: None)
    assert r2 == {"build_id": r1["build_id"], "reused": True}
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_snapshot FOR SYSTEM_TIME ALL")
        assert cur.fetchone()["n"] == 4
        cur.execute("SELECT COUNT(*) n FROM wiki_revision")
        assert cur.fetchone()["n"] == 5
    assert s1["revisions_new"] == 5


def test_conflicting_duplicate_is_rejected(db):
    load_records(db, base_records())
    bad = rev(2, "2020-01-10T12:00:00Z", article("Tampered text."))
    with pytest.raises(ConflictError):
        load_records(db, [bad])


def test_partial_fetch_window_is_not_published(db, embedder):
    recs = base_records() + [
        window("2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z", None, status="failed"),
        rev(6, "2020-03-05T00:00:00Z", article("Fetched before the API error.")),
    ]
    load_records(db, recs)
    build(db, embedder, log=lambda *_: None)
    assert status_rev(db, "2020-03-10T00:00:00Z") == ("not_covered", None)
    assert status_rev(db, "2020-01-15T00:00:00Z") == ("ok", 2)


def test_out_of_order_input_and_overlapping_windows(db, embedder):
    recs = [page(), window("2020-01-01T00:00:00Z", "2020-01-15T00:00:00Z", [1]),
            window("2020-01-10T00:00:00Z", "2020-02-01T00:00:00Z", [2])] + list(reversed(base_records()[2:]))
    stats = load_records(db, recs)
    assert stats["out_of_order"] > 0
    build(db, embedder, log=lambda *_: None)
    with db.cursor() as cur:
        cur.execute("SELECT coverage_start, coverage_end FROM wiki_coverage")
        assert cur.fetchall() == [{"coverage_start": D(2020, 1, 1), "coverage_end": D(2020, 2, 1)}]
    assert status_rev(db, "2020-01-12T00:00:00Z") == ("ok", 2)


def test_page_created_inside_window_has_hole(db, embedder):
    load_records(db, [page(), window("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z", []),
                      rev(7, "2020-01-05T00:00:00Z", article("Brand new article."))])
    build(db, embedder, log=lambda *_: None)
    assert status_rev(db, "2020-01-04T23:59:59Z") == ("no_revision", None)
    assert status_rev(db, "2020-01-05T00:00:00Z") == ("ok", 7)


def test_predecessor_mismatch_blocks_build(db, embedder):
    load_records(db, [page(), window("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z", [99]),
                      rev(1, "2019-12-15T10:00:00Z", article("x y z"))])
    with pytest.raises(BuildError):
        build(db, embedder, log=lambda *_: None)
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_active_build")
        assert cur.fetchone()["n"] == 0


def test_new_inputs_publish_new_build_atomically(db, embedder):
    load_records(db, base_records())
    r1 = build(db, embedder, log=lambda *_: None)
    load_records(db, [window("2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z", [5]),
                      rev(8, "2020-03-02T00:00:00Z", article("March version."))])
    r2 = build(db, embedder, log=lambda *_: None)
    assert r2["build_id"] != r1["build_id"]
    assert pinned_build(db) == r2["build_id"]
    with db.cursor() as cur:
        cur.execute("SELECT build_id, status FROM wiki_build ORDER BY build_id")
        assert [r["status"] for r in cur.fetchall()] == ["superseded", "published"]
    assert status_rev(db, "2020-03-01T00:00:00Z") == ("unavailable", 5)
    assert status_rev(db, "2020-03-03T00:00:00Z") == ("ok", 8)
