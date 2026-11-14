"""Bitemporal officeholder-tenure claims.

Pipeline (after a snapshot build is published):
  1. extract   -- per snapshot revision: deterministic infobox extractor; optional LLM extractor over prose
                  chunks (cached per unique chunk). Every claim keeps its exact source excerpt + revision.
  2. project   -- for every snapshot interval, the set of claims asserted by that revision; contiguous
                  presence becomes one row of wiki_claim_snapshot with an explicit historical system period.
  3. validate  -- AS OF checks at both ends of each row against the revision that asserts it; publish.
Query: FOR SYSTEM_TIME AS OF <Wikipedia time> + valid_from <= <date> < valid_to  ("as of Wikipedia's
revision at T, who did the article say held office on D?").

Date semantics (documented, explicit):
  * precision day/month/year/unknown; year "2012" as a start -> 2012-01-01 (precision year);
    as an end -> exclusive 2013-01-01 (precision year): the whole stated period is covered.
  * an end date stated to the day is exclusive (the successor takes office that day).
  * unknown start -> 0001-01-01 (precision unknown); ongoing end ("incumbent", "since") -> 9999-12-31.
  * unknown end (not stated, not current) -> the claim covers only its stated start granule
    (to_precision 'unknown'); it is never extended to later dates.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import mwparserfromhell

from .normalize import PARSER_VERSION, _clean_line, _render
from .projection import US, BuildError
from .timeutil import iso

INFOBOX_EXTRACTOR = "infobox-v1"
LLM_PROMPT_VERSION = "tenure-v1"
MIN_DATE, MAX_DATE = date(1, 1, 1), date(9999, 12, 31)
MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}
MON = "|".join(MONTHS)


# ---------------------------------------------------------------- dates

@dataclass(frozen=True)
class PDate:
    value: date | None
    precision: str  # day | month | year | unknown

    def as_start(self) -> tuple[date, str]:
        if self.value is None:
            return MIN_DATE, "unknown"
        return self.value, self.precision

    def as_end(self) -> tuple[date, str]:
        """Exclusive end covering the whole stated granule."""
        if self.value is None:
            return MAX_DATE, "unknown"
        v = self.value
        if self.precision == "year":
            return date(v.year + 1, 1, 1), "year"
        if self.precision == "month":
            return (date(v.year + 1, 1, 1) if v.month == 12 else date(v.year, v.month + 1, 1)), "month"
        return v, "day"


UNKNOWN = PDate(None, "unknown")


def parse_date_text(text: str) -> PDate:
    """'6 June 2019' | 'June 6, 2019' | 'June 2019' | '2019' | '2019-06-06' | '2019-06' -> PDate."""
    t = (text or "").strip()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return PDate(date(int(m[1]), int(m[2]), int(m[3])), "day")
    m = re.search(rf"\b(\d{{1,2}})\s+({MON})\s+(\d{{4}})\b", t, re.I)
    if m:
        return PDate(date(int(m[3]), MONTHS[m[2].lower()], int(m[1])), "day")
    m = re.search(rf"\b({MON})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", t, re.I)
    if m:
        return PDate(date(int(m[3]), MONTHS[m[1].lower()], int(m[2])), "day")
    m = re.search(r"\b(\d{4})-(\d{2})\b", t)
    if m:
        return PDate(date(int(m[1]), int(m[2]), 1), "month")
    m = re.search(rf"\b({MON})\s+(\d{{4}})\b", t, re.I)
    if m:
        return PDate(date(int(m[2]), MONTHS[m[1].lower()], 1), "month")
    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", t)
    if m:
        return PDate(date(int(m[1]), 1, 1), "year")
    return UNKNOWN


def parse_iso_partial(s: str | None) -> PDate:
    if not s:
        return UNKNOWN
    s = s.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return PDate(date.fromisoformat(s), "day")
        if re.fullmatch(r"\d{4}-\d{2}", s):
            return PDate(date(int(s[:4]), int(s[5:7]), 1), "month")
        if re.fullmatch(r"\d{4}", s):
            return PDate(date(int(s), 1, 1), "year")
    except ValueError:
        pass
    raise ValueError(f"bad partial date {s!r}")


# ---------------------------------------------------------------- claims

@dataclass(frozen=True)
class Claim:
    office: str
    holder: str
    valid_from: date
    valid_to: date
    from_precision: str
    to_precision: str
    end_status: str  # ended | ongoing | unknown

    def key(self, wiki_id: str, page_id: int) -> str:
        canon = [wiki_id, page_id, _norm(self.office), _norm(self.holder), self.valid_from.isoformat(),
                 self.valid_to.isoformat(), self.from_precision, self.to_precision, self.end_status]
        return hashlib.sha256(json.dumps(canon).encode()).hexdigest()


def make_claim(office: str, holder: str, start: PDate, end: PDate, end_status: str) -> Claim | None:
    vf, fp = start.as_start()
    if end.value is not None:
        vt, tp = end.as_end()
    elif end_status == "ongoing":
        # the revision says the tenure is current: open-ended as far as that revision is concerned
        vt, tp = MAX_DATE, "unknown"
    else:
        # unknown end: only the stated start granule is certain -- never extend it silently
        if start.value is None:
            return None
        granule_end = start.as_end()[0] if start.precision != "day" else start.value + timedelta(days=1)
        vt, tp = granule_end, "unknown"
    if not (vf < vt):
        return None
    return Claim(office.strip(), holder.strip(), vf, vt, fp, tp, end_status)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def office_aliases(page_title: str, infobox_post: str | None = None) -> set[str]:
    base = {_norm(page_title)}
    if infobox_post:
        base.add(_norm(infobox_post))
    for a in list(base):
        base.add(re.sub(r"\b(of|the)\b", " ", a))
        base.add(re.sub(r"\s+(of|in)\s+\w+$", "", a))  # "prime minister of finland" -> "prime minister"
        base.add(a.replace("of the republic", "").strip())
    return {re.sub(r"\s+", " ", a).strip() for a in base if a.strip()}


def matches_office(office: str, aliases: set[str]) -> bool:
    o = _norm(office)
    return any(a and (a in o or o in a) for a in aliases) and len(o) >= 4


# ---------------------------------------------------------------- deterministic infobox extractor

def _param(t, *names: str) -> tuple[str, str] | None:
    for n in names:
        if t.has(n):
            raw = str(t.get(n).value)
            val = _clean_line(_render(t.get(n).value))
            if val:
                return val, raw
    return None


def extract_infobox(wikitext: str, page_title: str) -> list[tuple[Claim, str]]:
    """Claims from {{Infobox official post}} (incumbent/inaugural) and {{Infobox officeholder}} terms.

    Returns (claim, excerpt) where excerpt is the exact infobox source lines.
    """
    out: list[tuple[Claim, str]] = []
    code = mwparserfromhell.parse(wikitext or "")
    for t in code.filter_templates(recursive=False):
        name = _norm(str(t.name))
        if name.startswith("infobox official post") or name.startswith("infobox political post"):
            post = _param(t, "post", "body")
            office = page_title
            inc = _param(t, "incumbent")
            since = _param(t, "incumbentsince")
            if inc:
                start = parse_date_text(since[0]) if since else UNKNOWN
                c = make_claim(office, inc[0], start, UNKNOWN, "ongoing")
                if c:
                    excerpt = f"| incumbent = {inc[1].strip()}" + (f"\n| incumbentsince = {since[1].strip()}" if since else "")
                    out.append((c, excerpt))
            first = _param(t, "inaugural", "first")
            formation = _param(t, "formation")
            if first:
                start = parse_date_text(formation[0]) if formation else UNKNOWN
                c = make_claim(office, first[0], start, UNKNOWN, "unknown")
                if c:
                    excerpt = f"| inaugural = {first[1].strip()}" + (f"\n| formation = {formation[1].strip()}" if formation else "")
                    out.append((c, excerpt))
            _ = post
        elif name.startswith("infobox officeholder") or name.startswith("infobox politician"):
            for i in [""] + [str(n) for n in range(1, 13)]:
                office = _param(t, f"office{i}")
                ts = _param(t, f"term_start{i}")
                if not office or not ts:
                    continue
                te = _param(t, f"term_end{i}")
                end = parse_date_text(te[0]) if te else UNKNOWN
                status = "ended" if end.value else ("ongoing" if not te else "unknown")
                c = make_claim(office[0], page_title, parse_date_text(ts[0]), end, status)
                if c:
                    out.append((c, f"| office{i} = {office[1].strip()}\n| term_start{i} = {ts[1].strip()}"
                                   + (f"\n| term_end{i} = {te[1].strip()}" if te else "")))
    return out


def infobox_post(wikitext: str) -> str | None:
    for t in mwparserfromhell.parse(wikitext or "").filter_templates(recursive=False):
        if _norm(str(t.name)).startswith("infobox official post"):
            p = _param(t, "post")
            return p[0] if p else None
    return None


# ---------------------------------------------------------------- LLM extractor (validated)

LLM_SYSTEM = """You extract officeholder tenure claims from one passage of a Wikipedia article.
Only extract claims the passage itself states: a named person holding a named office during a stated
time. Do not use outside knowledge. Do not infer dates that are not written in the passage.
For each claim copy one sentence (or a contiguous span) from the passage VERBATIM as "excerpt".
Dates: "YYYY-MM-DD", "YYYY-MM" or "YYYY" exactly as precise as the passage; null if not stated.
end_status: "ended" if the passage gives an end, "ongoing" if it says the person currently holds the
office (incumbent/current/since), otherwise "unknown". Return {"claims": []} if there are none."""

LLM_SCHEMA = {"type": "object", "properties": {"claims": {"type": "array", "maxItems": 12, "items": {
    "type": "object", "properties": {
        "holder": {"type": "string"}, "office": {"type": "string"},
        "start": {"type": ["string", "null"]}, "end": {"type": ["string", "null"]},
        "end_status": {"type": "string", "enum": ["ended", "ongoing", "unknown"]},
        "excerpt": {"type": "string"}},
    "required": ["holder", "office", "start", "end", "end_status", "excerpt"], "additionalProperties": False}}},
    "required": ["claims"], "additionalProperties": False}


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _date_visible(p: PDate, excerpt: str) -> bool:
    if p.value is None:
        return True
    if str(p.value.year) not in excerpt:
        return False
    if p.precision in ("day", "month"):
        month = [m for m, i in MONTHS.items() if i == p.value.month][0]
        if month not in excerpt.lower() and f"{p.value.year}-{p.value.month:02d}" not in excerpt:
            return False
    if p.precision == "day" and not re.search(rf"\b0?{p.value.day}\b", excerpt):
        return False
    return True


def validate_llm_claims(raw: dict, chunk_text: str, aliases: set[str], canonical_office: str) -> tuple[list[tuple[Claim, str]], list[str]]:
    accepted, rejected = [], []
    text = _ws(chunk_text)
    for c in (raw or {}).get("claims", []) if isinstance(raw, dict) else []:
        try:
            excerpt = _ws(str(c["excerpt"]))
            holder, office = _ws(str(c["holder"])), _ws(str(c["office"]))
            if len(excerpt) < 10 or excerpt not in text:
                rejected.append(f"excerpt not verbatim: {excerpt[:60]!r}")
                continue
            if not holder or _norm(holder) not in _norm(excerpt):
                rejected.append(f"holder not in excerpt: {holder!r}")
                continue
            if not matches_office(office, aliases):
                rejected.append(f"other office: {office!r}")
                continue
            start, end = parse_iso_partial(c.get("start")), parse_iso_partial(c.get("end"))
            if not (_date_visible(start, excerpt) and _date_visible(end, excerpt)):
                rejected.append("date not visible in excerpt")
                continue
            status = c.get("end_status")
            if status not in ("ended", "ongoing", "unknown"):
                rejected.append("bad end_status")
                continue
            if status == "ended" and end.value is None:
                status = "unknown"
            if end.value is not None:
                status = "ended"
            claim = make_claim(canonical_office, holder, start, end, status)
            if claim is None:
                rejected.append("empty or reversed period")
                continue
            accepted.append((claim, excerpt))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(f"malformed: {exc}")
    return accepted, rejected


CLAIMY = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def llm_extract_chunks(llm, chunks: list[dict], aliases: set[str], canonical_office: str, concurrency: int = 4,
                       log: Callable[[str], None] = print) -> dict[int, dict]:
    """chunks: [{chunk_id, text, section}] -> {chunk_id: {status, accepted:[(Claim, excerpt)], rejected}}"""

    def one(ch):
        messages = [{"role": "system", "content": LLM_SYSTEM},
                    {"role": "user", "content": f"Article office of interest: {canonical_office}\n"
                                                f"Section: {ch['section'] or 'Lead'}\nPassage:\n{ch['text']}"}]
        try:
            from .answer import _extract_json
            out = llm(messages, LLM_SCHEMA) if getattr(llm, "structured", False) else llm(messages)
            acc, rej = validate_llm_claims(_extract_json(out), ch["text"], aliases, canonical_office)
            return ch["chunk_id"], {"status": "ok", "accepted": acc, "rejected": rej}
        except Exception as exc:  # noqa: BLE001 - outages are recorded, never fatal
            return ch["chunk_id"], {"status": "error", "accepted": [], "rejected": [f"{type(exc).__name__}: {exc}"[:300]]}

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (cid, res) in enumerate(pool.map(one, chunks), 1):
            results[cid] = res
            log(f"  llm chunk {i}/{len(chunks)}: {res['status']}, {len(res['accepted'])} accepted, {len(res['rejected'])} rejected")
    return results


# ---------------------------------------------------------------- extraction into tables

def llm_extractor_id(model: str) -> str:
    return f"llm:{model}:{LLM_PROMPT_VERSION}"


def _claim_id(cur, wiki_id: str, page_id: int, c: Claim) -> int:
    k = c.key(wiki_id, page_id)
    cur.execute("SELECT claim_id FROM wiki_claim WHERE claim_key=%s", (k,))
    row = cur.fetchone()
    if row:
        return row["claim_id"]
    cur.execute("INSERT INTO wiki_claim (claim_key, wiki_id, page_id, office, holder, valid_from, valid_to, from_precision, "
                "to_precision, end_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (k, wiki_id, page_id, c.office, c.holder, c.valid_from, c.valid_to, c.from_precision, c.to_precision, c.end_status))
    return cur.lastrowid


def extract_for_build(conn, build_id: int, page_keys: list[tuple[str, int]], llm=None, llm_model: str = "",
                      concurrency: int = 4, log: Callable[[str], None] = print) -> dict:
    stats = {"revisions": 0, "infobox_claims": 0, "llm_chunks_called": 0, "llm_chunks_cached": 0,
             "llm_accepted": 0, "llm_rejected": 0, "llm_errors": 0}
    ext_id = llm_extractor_id(llm_model) if llm else None
    with conn.cursor() as cur:
        for wiki_id, page_id in page_keys:
            cur.execute("SELECT title FROM wiki_page WHERE wiki_id=%s AND page_id=%s", (wiki_id, page_id))
            title = cur.fetchone()["title"]
            cur.execute("SELECT DISTINCT s.revision_id FROM wiki_snapshot FOR SYSTEM_TIME ALL s WHERE s.build_id=%s "
                        "AND s.wiki_id=%s AND s.page_id=%s AND s.content_available ORDER BY s.revision_id", (build_id, wiki_id, page_id))
            rids = [r["revision_id"] for r in cur.fetchall()]
            posts = set()
            for rid in rids:
                cur.execute("SELECT wikitext FROM wiki_revision WHERE wiki_id=%s AND revision_id=%s", (wiki_id, rid))
                wt = cur.fetchone()["wikitext"]
                p = infobox_post(wt)
                if p:
                    posts.add(p)
                stats["revisions"] += 1
                for claim, excerpt in extract_infobox(wt, title):
                    cid = _claim_id(cur, wiki_id, page_id, claim)
                    cur.execute("INSERT IGNORE INTO wiki_revision_claim (wiki_id, revision_id, claim_id, extractor, source_ref, excerpt) "
                                "VALUES (%s,%s,%s,%s,'infobox',%s)", (wiki_id, rid, cid, INFOBOX_EXTRACTOR, excerpt))
                    stats["infobox_claims"] += cur.rowcount
            if not llm:
                continue
            aliases = set()
            for p in posts | {title}:
                aliases |= office_aliases(title, p)
            # unique chunks of all snapshot revisions, prefiltered to passages mentioning a year
            cur.execute("SELECT DISTINCT c.chunk_id, c.text, rc.section_heading FROM wiki_revision_chunk rc JOIN wiki_chunk c "
                        "ON c.chunk_id=rc.chunk_id WHERE rc.wiki_id=%s AND rc.parser_version=%s AND rc.revision_id IN ("
                        + ",".join(["%s"] * len(rids)) + ")", (wiki_id, PARSER_VERSION, *rids))
            chunks = {}
            for r in cur.fetchall():
                if CLAIMY.search(r["text"]) and any(a.split()[0] in _norm(r["text"]) for a in aliases if a):
                    chunks.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"], "text": r["text"], "section": r["section_heading"]})
            cur.execute("SELECT chunk_id FROM wiki_claim_extraction WHERE extractor=%s AND status='ok'", (ext_id,))
            done = {r["chunk_id"] for r in cur.fetchall()}
            todo = [c for cid, c in chunks.items() if cid not in done]
            stats["llm_chunks_cached"] += len(chunks) - len(todo)
            stats["llm_chunks_called"] += len(todo)
            log(f"page {wiki_id}:{page_id}: {len(chunks)} candidate chunks, {len(todo)} need the model")
            results = llm_extract_chunks(llm, todo, aliases, title, concurrency, log) if todo else {}
            for cid, res in results.items():
                serial = {"accepted": [{"claim": {**c.__dict__, "valid_from": c.valid_from.isoformat(),
                                                  "valid_to": c.valid_to.isoformat()}, "excerpt": e} for c, e in res["accepted"]],
                          "rejected": res["rejected"]}
                cur.execute("REPLACE INTO wiki_claim_extraction (chunk_id, extractor, status, accepted, rejected, result) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", (cid, ext_id, res["status"], len(res["accepted"]), len(res["rejected"]),
                                                           json.dumps(serial)))
                stats["llm_accepted"] += len(res["accepted"])
                stats["llm_rejected"] += len(res["rejected"]) if res["status"] == "ok" else 0
                stats["llm_errors"] += res["status"] == "error"
            # map cached chunk claims to every revision containing the chunk
            if chunks:
                cur.execute("SELECT chunk_id, result FROM wiki_claim_extraction WHERE extractor=%s AND status='ok' AND chunk_id IN ("
                            + ",".join(["%s"] * len(chunks)) + ")", (ext_id, *chunks))
                for r in cur.fetchall():
                    for item in json.loads(r["result"])["accepted"]:
                        d = item["claim"]
                        claim = Claim(d["office"], d["holder"], date.fromisoformat(d["valid_from"]), date.fromisoformat(d["valid_to"]),
                                      d["from_precision"], d["to_precision"], d["end_status"])
                        cid = _claim_id(cur, wiki_id, page_id, claim)
                        cur.execute("INSERT IGNORE INTO wiki_revision_claim (wiki_id, revision_id, claim_id, extractor, source_ref, excerpt) "
                                    "SELECT rc.wiki_id, rc.revision_id, %s, %s, %s, %s FROM wiki_revision_chunk rc "
                                    "WHERE rc.wiki_id=%s AND rc.chunk_id=%s AND rc.revision_id IN (" + ",".join(["%s"] * len(rids)) + ")",
                                    (cid, ext_id, f"chunk:{r['chunk_id']}", item["excerpt"], wiki_id, r["chunk_id"], *rids))
    return stats


# ---------------------------------------------------------------- bitemporal projection

def project_claims(conn, build_id: int, extractors: list[str], log: Callable[[str], None] = print) -> dict:
    """`conn`: importer session with system_versioning_insert_history=ON."""
    with conn.cursor() as cur:
        cur.execute("SELECT wiki_id, page_id, revision_id, row_start, row_end FROM wiki_snapshot FOR SYSTEM_TIME ALL "
                    "WHERE build_id=%s ORDER BY wiki_id, page_id, row_start", (build_id,))
        intervals = cur.fetchall()
        ph = ",".join(["%s"] * len(extractors))
        cur.execute(f"SELECT DISTINCT rc.wiki_id, rc.revision_id, rc.claim_id FROM wiki_revision_claim rc WHERE rc.extractor IN ({ph})",
                    tuple(extractors))
        asserted: dict[tuple[str, int], set[int]] = {}
        for r in cur.fetchall():
            asserted.setdefault((r["wiki_id"], r["revision_id"]), set()).add(r["claim_id"])
        # contiguous presence spans per claim
        spans: dict[tuple[str, int, int], list[list]] = {}
        for iv in intervals:
            for cid in asserted.get((iv["wiki_id"], iv["revision_id"]), ()):
                key = (iv["wiki_id"], iv["page_id"], cid)
                lst = spans.setdefault(key, [])
                if lst and lst[-1][1] == iv["row_start"]:
                    lst[-1][1] = iv["row_end"]
                else:
                    lst.append([iv["row_start"], iv["row_end"]])
        fp = hashlib.sha256(json.dumps([build_id, sorted(extractors), sorted(
            [k[0], k[1], k[2], [[a.isoformat(), b.isoformat()] for a, b in v]] for k, v in spans.items())]).encode()).hexdigest()
        cur.execute("SELECT claim_build_id FROM wiki_claim_build WHERE build_id=%s AND fingerprint=%s AND status='published'", (build_id, fp))
        same = cur.fetchone()
        if same:
            log(f"claims unchanged; reusing claim build {same['claim_build_id']}")
            return {"claim_build_id": same["claim_build_id"], "reused": True}
        cur.execute("INSERT INTO wiki_claim_build (build_id, fingerprint, extractors, status) VALUES (%s,%s,%s,'building')",
                    (build_id, fp, ",".join(sorted(extractors))))
        cbid = cur.lastrowid
        claims = {}
        if spans:
            ids = sorted({k[2] for k in spans})
            cur.execute("SELECT * FROM wiki_claim WHERE claim_id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids))
            claims = {r["claim_id"]: r for r in cur.fetchall()}
        rows = []
        for (wiki_id, page_id, cid), lst in spans.items():
            c = claims[cid]
            for s, e in lst:
                rows.append((cbid, wiki_id, page_id, cid, c["office"], c["holder"], c["valid_from"], c["valid_to"],
                             c["from_precision"], c["to_precision"], c["end_status"], s, e))
        if rows:
            cur.executemany("INSERT INTO wiki_claim_snapshot (claim_build_id, wiki_id, page_id, claim_id, office, holder, valid_from, "
                            "valid_to, from_precision, to_precision, end_status, row_start, row_end) VALUES "
                            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        report = validate_claims(conn, cbid, build_id, extractors, spans)
        if not report["ok"]:
            cur.execute("UPDATE wiki_claim_build SET status='failed', validation=%s WHERE claim_build_id=%s", (json.dumps(report), cbid))
            raise BuildError(f"claim build {cbid} failed validation: {report['failures'][:5]}")
        conn.begin()
        cur.execute("UPDATE wiki_claim_build SET status='superseded' WHERE build_id=%s AND status='published'", (build_id,))
        cur.execute("UPDATE wiki_claim_build SET status='published', rows_written=%s, validation=%s WHERE claim_build_id=%s",
                    (len(rows), json.dumps(report), cbid))
        conn.commit()
    log(f"published claim build {cbid}: {len(rows)} bitemporal rows, {report['checks']} checks passed")
    return {"claim_build_id": cbid, "reused": False, "rows": len(rows), "validation": report}


def validate_claims(conn, cbid: int, build_id: int, extractors: list[str], spans: dict) -> dict:
    failures, checks = [], 0
    ph = ",".join(["%s"] * len(extractors))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM wiki_claim_snapshot WHERE claim_build_id=%s", (cbid,))
        checks += 1
        if cur.fetchone()["n"]:
            failures.append("open-ended current rows present")
        for (wiki_id, page_id, cid), lst in spans.items():
            for s, e in lst:
                for t in (s, e - US):
                    checks += 1
                    cur.execute("SELECT COUNT(*) n FROM wiki_claim_snapshot FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6)) "
                                "WHERE claim_build_id=%s AND wiki_id=%s AND page_id=%s AND claim_id=%s", (t, cbid, wiki_id, page_id, cid))
                    if cur.fetchone()["n"] != 1:
                        failures.append(f"claim {cid} missing AS OF {t}")
                    # the revision current at t (snapshot SVT) must itself assert the claim
                    cur.execute("SELECT s.revision_id FROM wiki_snapshot FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6)) s "
                                f"JOIN wiki_revision_claim rc ON rc.wiki_id=s.wiki_id AND rc.revision_id=s.revision_id AND rc.claim_id=%s "
                                f"AND rc.extractor IN ({ph}) WHERE s.build_id=%s AND s.wiki_id=%s AND s.page_id=%s LIMIT 1",
                                (t, cid, *extractors, build_id, wiki_id, page_id))
                    checks += 1
                    if not cur.fetchone():
                        failures.append(f"claim {cid} AS OF {t} not asserted by the current revision")
                checks += 1
                cur.execute("SELECT COUNT(*) n FROM wiki_claim_snapshot FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6)) "
                            "WHERE claim_build_id=%s AND wiki_id=%s AND page_id=%s AND claim_id=%s", (e, cbid, wiki_id, page_id, cid))
                if cur.fetchone()["n"] != 0 and not any(s2 == e for s2, _ in lst):
                    failures.append(f"claim {cid} visible after its span end {e}")
    return {"ok": not failures, "checks": checks, "failures": failures}


# ---------------------------------------------------------------- queries

BITEMPORAL_SQL = """
SELECT claim_id, office, holder, valid_from, valid_to, from_precision, to_precision, end_status, row_start, row_end
FROM wiki_claim_snapshot FOR SYSTEM_TIME AS OF CAST(%s AS DATETIME(6))
WHERE claim_build_id = %s AND wiki_id = %s AND page_id = %s
  AND valid_from <= %s AND valid_to > %s
ORDER BY valid_from, holder
""".strip()

HISTORY_SQL = """
SELECT claim_id, holder, valid_from, valid_to, from_precision, to_precision, end_status, row_start, row_end
FROM wiki_claim_snapshot FOR SYSTEM_TIME ALL
WHERE claim_build_id = %s AND wiki_id = %s AND page_id = %s
  AND valid_from <= %s AND valid_to > %s
ORDER BY row_start, holder
""".strip()


def active_claim_build(conn, build_id: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute("SELECT claim_build_id FROM wiki_claim_build WHERE build_id=%s AND status='published' "
                    "ORDER BY claim_build_id DESC LIMIT 1", (build_id,))
        r = cur.fetchone()
    return r["claim_build_id"] if r else None


def fmt_range(row: dict) -> str:
    def f(d: date, p: str, end: bool) -> str:
        if p == "unknown":
            return "unknown" if not end else ("present" if row["end_status"] == "ongoing" else "end unknown")
        if end:  # exclusive storage -> display the stated granule
            d = d - timedelta(days=1) if p == "day" else d - timedelta(days=1)
        return {"day": d.isoformat(), "month": d.strftime("%Y-%m"), "year": str(d.year)}[p]
    if row["to_precision"] == "unknown":
        end_txt = f(row["valid_to"], "unknown", True)
    elif row["to_precision"] == "day":
        end_txt = f"before {row['valid_to'].isoformat()}"
    else:
        end_txt = f(row["valid_to"], row["to_precision"], True)
    return f"{f(row['valid_from'], row['from_precision'], False)} → {end_txt}"


def claims_as_of(conn, build_id: int, cbid: int, wiki_id: str, page_id: int, as_of: datetime, valid_at: date,
                 revision_id: int | None, url: str | None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(BITEMPORAL_SQL, (as_of, cbid, wiki_id, page_id, valid_at, valid_at))
        rows = cur.fetchall()
        out = []
        for r in rows:
            sources = []
            if revision_id:
                cur.execute("SELECT extractor, source_ref, excerpt FROM wiki_revision_claim WHERE wiki_id=%s AND revision_id=%s "
                            "AND claim_id=%s ORDER BY extractor", (wiki_id, revision_id, r["claim_id"]))
                sources = [{"extractor": s["extractor"], "source": s["source_ref"], "excerpt": s["excerpt"],
                            "revision_id": revision_id, "url": url} for s in cur.fetchall()]
            out.append({"claim_id": r["claim_id"], "office": r["office"], "holder": r["holder"],
                        "valid_from": r["valid_from"].isoformat(), "valid_to": r["valid_to"].isoformat(),
                        "from_precision": r["from_precision"], "to_precision": r["to_precision"], "end_status": r["end_status"],
                        "display": fmt_range(r), "asserted_since": iso(r["row_start"]), "asserted_until": iso(r["row_end"]),
                        "sources": sources})
    return out


def attach_timeline_claims(conn, build_id: int, cbid: int, wiki_id: str, page_id: int,
                          states: list[dict]) -> None:
    """Attach only published claims, with evidence from each timeline revision itself."""
    if not states:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT extractors FROM wiki_claim_build WHERE claim_build_id=%s", (cbid,))
        extractors = cur.fetchone()["extractors"].split(",")
        revision_ids = sorted({s["revision_id"] for s in states})
        ids = ",".join(["%s"] * len(revision_ids))
        ex = ",".join(["%s"] * len(extractors))
        cur.execute(
            "SELECT s.revision_id, c.*, rc.extractor, rc.excerpt FROM wiki_snapshot FOR SYSTEM_TIME ALL s "
            "JOIN wiki_claim_snapshot FOR SYSTEM_TIME ALL c ON c.wiki_id=s.wiki_id AND c.page_id=s.page_id "
            "AND c.row_start<=s.row_start AND c.row_end>s.row_start AND c.claim_build_id=%s "
            "JOIN wiki_revision_claim rc ON rc.wiki_id=s.wiki_id AND rc.revision_id=s.revision_id "
            "AND rc.claim_id=c.claim_id "
            f"WHERE s.build_id=%s AND s.wiki_id=%s AND s.page_id=%s AND s.revision_id IN ({ids}) "
            f"AND rc.extractor IN ({ex}) ORDER BY s.row_start, c.holder, c.claim_id, rc.extractor",
            (cbid, build_id, wiki_id, page_id, *revision_ids, *extractors))
        by_revision = {}
        for r in cur.fetchall():
            claims = by_revision.setdefault(r["revision_id"], {})
            claim = claims.setdefault(r["claim_id"], {
                "claim_id": r["claim_id"], "office": r["office"], "holder": r["holder"],
                "display": fmt_range(r), "end_status": r["end_status"], "sources": []})
            source = {"extractor": r["extractor"], "excerpt": r["excerpt"], "revision_id": r["revision_id"]}
            if source not in claim["sources"]:
                claim["sources"].append(source)
    for state in states:
        state["claims"] = list(by_revision.get(state["revision_id"], {}).values())
        for claim in state["claims"]:
            for source in claim["sources"]:
                source["url"] = state["url"]


def claim_history(conn, cbid: int, wiki_id: str, page_id: int, valid_at: date) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(HISTORY_SQL, (cbid, wiki_id, page_id, valid_at, valid_at))
        return [{"claim_id": r["claim_id"], "holder": r["holder"], "display": fmt_range(r), "end_status": r["end_status"],
                 "asserted_from": iso(r["row_start"]), "asserted_until": iso(r["row_end"])} for r in cur.fetchall()]


# ---------------------------------------------------------------- portable LLM extraction cache (fixture)

def export_extraction_cache(conn, path: str) -> int:
    """Write validated LLM extraction results keyed by chunk text hash (portable across databases)."""
    import gzip
    with conn.cursor() as cur:
        cur.execute("SELECT c.text_hash, c.parser_version, e.extractor, e.accepted, e.rejected, e.result FROM wiki_claim_extraction e "
                    "JOIN wiki_chunk c ON c.chunk_id=e.chunk_id WHERE e.status='ok' ORDER BY c.text_hash, e.extractor")
        rows = cur.fetchall()
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    return len(rows)


def import_extraction_cache(conn, path: str) -> dict:
    import gzip
    stats = {"imported": 0, "no_matching_chunk": 0}
    with gzip.open(path, "rt", encoding="utf-8") as fh, conn.cursor() as cur:
        for line in fh:
            r = json.loads(line)
            cur.execute("SELECT chunk_id FROM wiki_chunk WHERE text_hash=%s AND parser_version=%s", (r["text_hash"], r["parser_version"]))
            ids = [x["chunk_id"] for x in cur.fetchall()]
            if not ids:
                stats["no_matching_chunk"] += 1
                continue
            for cid in ids:
                cur.execute("INSERT IGNORE INTO wiki_claim_extraction (chunk_id, extractor, status, accepted, rejected, result) "
                            "VALUES (%s,%s,'ok',%s,%s,%s)", (cid, r["extractor"], r["accepted"], r["rejected"], r["result"]))
                stats["imported"] += cur.rowcount
    return stats
