"""Deterministic section/paragraph alignment with a word-level diff for matched text."""
from __future__ import annotations

import difflib
import re

PAIR_THRESHOLD = 0.45
_TOKEN = re.compile(r"\s+|[\w’'\-.,%€$]+|[^\w\s]", re.UNICODE)


def _norm_heading(h: str) -> str:
    return re.sub(r"\W+", " ", h.lower()).strip()


def word_diff(a: str, b: str) -> list[dict]:
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    ops: list[dict] = []

    def push(op: str, text: str) -> None:
        if not text:
            return
        if ops and ops[-1]["op"] == op:
            ops[-1]["text"] += text
        else:
            ops.append({"op": op, "text": text})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            push("eq", "".join(ta[i1:i2]))
        else:
            push("del", "".join(ta[i1:i2]))
            push("ins", "".join(tb[j1:j2]))
    return ops


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a.split(), b=b.split(), autojunk=False).ratio()


def _align_paragraphs(pa: list[str], pb: list[str]) -> list[dict]:
    out: list[dict] = []
    sm = difflib.SequenceMatcher(a=pa, b=pb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append({"status": "same", "before": pa[i1 + k], "after": pb[j1 + k]})
            continue
        left, right = list(range(i1, i2)), list(range(j1, j2))
        # Greedy in-order pairing of similar paragraphs inside a replace block.
        jstart = 0
        for i in left:
            best, best_j = 0.0, None
            for jj in range(jstart, len(right)):
                r = _ratio(pa[i], pb[right[jj]])
                if r > best:
                    best, best_j = r, jj
            if best_j is not None and best >= PAIR_THRESHOLD:
                for jj in range(jstart, best_j):
                    out.append({"status": "added", "before": None, "after": pb[right[jj]]})
                out.append({"status": "changed", "before": pa[i], "after": pb[right[best_j]],
                            "words": word_diff(pa[i], pb[right[best_j]])})
                jstart = best_j + 1
            else:
                out.append({"status": "removed", "before": pa[i], "after": None})
        for jj in range(jstart, len(right)):
            out.append({"status": "added", "before": None, "after": pb[right[jj]]})
    return out


def diff_sections(before: list[dict], after: list[dict]) -> dict:
    ka = [_norm_heading(s["heading"]) for s in before]
    kb = [_norm_heading(s["heading"]) for s in after]
    sm = difflib.SequenceMatcher(a=ka, b=kb, autojunk=False)
    sections: list[dict] = []
    stats = {"paragraphs_same": 0, "paragraphs_changed": 0, "paragraphs_added": 0,
             "paragraphs_removed": 0, "sections_added": 0, "sections_removed": 0}

    def emit_pair(sa: dict, sb: dict) -> None:
        paras = _align_paragraphs(sa["paragraphs"], sb["paragraphs"])
        for p in paras:
            stats["paragraphs_" + p["status"]] += 1
        changed = any(p["status"] != "same" for p in paras) or sa["heading"] != sb["heading"]
        sections.append({"status": "changed" if changed else "same", "heading_before": sa["heading"],
                         "heading_after": sb["heading"], "paragraphs": paras})

    def emit_one(s: dict, status: str) -> None:
        key = "after" if status == "added" else "before"
        other = "before" if key == "after" else "after"
        stats["sections_" + status] += 1
        stats["paragraphs_" + status] += len(s["paragraphs"])
        sections.append({"status": status, "heading_before": s["heading"] if status == "removed" else None,
                         "heading_after": s["heading"] if status == "added" else None,
                         "paragraphs": [{"status": status, key: p, other: None} for p in s["paragraphs"]]})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                emit_pair(before[i1 + k], after[j1 + k])
        else:
            # Moved/renamed/unmatched sections are shown as simple delete/add pairs.
            for i in range(i1, i2):
                emit_one(before[i], "removed")
            for j in range(j1, j2):
                emit_one(after[j], "added")
    identical = all(s["status"] == "same" for s in sections)
    return {"identical": identical, "stats": stats, "sections": sections}
