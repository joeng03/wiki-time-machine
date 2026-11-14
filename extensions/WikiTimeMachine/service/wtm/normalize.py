"""Deterministic wikitext -> extracted text (sections/paragraphs) -> chunks.

Uses mwparserfromhell (pinned). Templates are NOT expanded (that would pull in dependencies from
outside the revision); a small whitelist of inline templates whose visible text matters
(convert, as of, lang, dates, dashes...) is rendered locally, everything else is dropped.
Output is labelled "extracted text" in the UI; the original revision link is authoritative.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import mwparserfromhell
from mwparserfromhell.nodes import (Argument, Comment, ExternalLink, Heading, HTMLEntity, Tag,
                                    Template, Text, Wikilink)

PARSER_VERSION = "wtm-norm-2/mwph-" + mwparserfromhell.__version__

SKIP_SECTIONS = {
    "references", "external links", "see also", "further reading", "notes", "sources",
    "bibliography", "footnotes", "citations", "notes and references", "references and notes",
    "literature", "works cited", "gallery",
}
DROP_TAGS = {
    "ref", "references", "table", "gallery", "math", "score", "timeline", "imagemap",
    "templatedata", "syntaxhighlight", "source", "graph", "mapframe", "maplink", "chem", "ce",
    "categorytree", "inputbox", "hiero", "noinclude", "includeonly", "style", "templatestyles",
}
DROP_LINK_NS = {"file", "image", "category", "media", "wikt", "wiktionary", "commons", "s",
                "special", "template", "help", "wp", "wikipedia", "portal", "draft", "user",
                "talk", "mos", "q", "n", "b", "v", "voy"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
LIST_MARK = "\x00"

TARGET_WORDS = 240      # ~320 tokens
MAX_WORDS = 270         # ~350 tokens
MIN_WORDS = 150         # ~200 tokens (except section tails)


def _pos(t: Template, i: int) -> str:
    """i-th (1-based) positional parameter rendered as plain text."""
    n = 0
    for p in t.params:
        if p.showkey:
            continue
        n += 1
        if n == i:
            return _render(p.value).strip()
    return ""


def _named(t: Template, name: str) -> str:
    if t.has(name):
        return _render(t.get(name).value).strip()
    return ""


def _fmt_date(y: str, m: str = "", d: str = "") -> str:
    y, m, d = y.strip(), m.strip(), d.strip()
    if m.isdigit() and 1 <= int(m) <= 12:
        m = MONTHS[int(m) - 1]
    return " ".join(x for x in (d.lstrip("0") if d.isdigit() else d, m, y) if x)


def _render_template(t: Template) -> str:
    name = str(t.name).strip().replace("_", " ")
    lname = name.lower()
    if lname in ("convert", "cvt"):
        a, b = _pos(t, 1), _pos(t, 2)
        if b in ("to", "-", "–", "and", "by", "x", "or", "+/-"):
            return f"{a} {b} {_pos(t, 3)} {_pos(t, 4)}".strip()
        return f"{a} {b}".strip()
    if lname in ("as of", "asof"):
        return "As of " + _fmt_date(_pos(t, 1), _pos(t, 2), _pos(t, 3))
    if lname in ("start date", "start date and age", "end date", "end date and age", "dts",
                 "birth date", "death date", "date", "film date", "opening date"):
        p1 = _pos(t, 1)
        if p1.isdigit() and len(p1) == 4:
            return _fmt_date(p1, _pos(t, 2), _pos(t, 3))
        return p1
    if lname in ("circa", "c."):
        return "c. " + _pos(t, 1)
    if lname == "lang" or lname.startswith("lang-") or lname in ("langx", "transl", "transliteration"):
        if lname in ("lang", "langx", "transl", "transliteration"):
            return _pos(t, 2)
        return _pos(t, 1)
    if lname in ("nowrap", "nobr", "nobreak", "small", "smaller", "big", "nobold", "noitalic",
                 "abbr", "ill", "interlanguage link", "visible anchor", "vanchor", "val",
                 "keypress", "em", "strong", "sic", "flag", "flagcountry", "nihongo", "mono",
                 "proper name", "angbr", "w", "linktext", "tooltip", "highlight"):
        return _pos(t, 1)
    if lname in ("frac", "fraction"):
        a, b, c = _pos(t, 1), _pos(t, 2), _pos(t, 3)
        if c:
            return f"{a} {b}/{c}"
        return f"{a}/{b}" if b else f"1/{a}"
    if lname in ("us$", "usd"):
        return "US$" + _pos(t, 1)
    if lname in ("€", "eur", "euro"):
        return "€" + _pos(t, 1)
    if lname in ("ndash", "snd", "spaced ndash", "–"):
        return " – " if "s" in lname else "–"
    if lname in ("mdash", "spaced mdash", "—"):
        return "—"
    if lname in ("nbsp", "thinsp", "spaces", "space"):
        return " "
    if lname in ("'", "' \""):
        return "'"
    if lname in ("okina",):
        return "ʻ"
    if lname in ("quote", "blockquote", "quotation", "cquote"):
        q = _named(t, "text") or _named(t, "quote") or _pos(t, 1)
        return f"\n\n{q}\n\n" if q else ""
    return ""  # infoboxes, citations, navboxes, maintenance tags, unsupported transclusions


def _render_link(link: Wikilink) -> str:
    title = str(link.title).strip()
    lead = title.lstrip(":")
    if ":" in lead:
        prefix = lead.split(":", 1)[0].strip().lower()
        if prefix in DROP_LINK_NS or (len(prefix) in (2, 3) and prefix.isalpha() and not title.startswith(":")):
            return ""
    if link.text is not None:
        return _render(link.text)
    return lead.split("#", 1)[0] if not lead.startswith("#") else lead[1:]


def _render(wc) -> str:
    out: list[str] = []
    for node in wc.nodes:
        if isinstance(node, Text):
            out.append(str(node))
        elif isinstance(node, (Comment, Argument, Heading)):
            continue
        elif isinstance(node, Template):
            out.append(_render_template(node))
        elif isinstance(node, Wikilink):
            out.append(_render_link(node))
        elif isinstance(node, ExternalLink):
            out.append(_render(node.title) if node.title is not None else "")
        elif isinstance(node, HTMLEntity):
            out.append(node.normalize())
        elif isinstance(node, Tag):
            tag = str(node.tag).strip().lower()
            if tag in DROP_TAGS:
                continue
            if tag == "br":
                out.append(" ")
            elif tag in ("li", "dt", "dd"):
                out.append(LIST_MARK)
            elif tag == "hr":
                out.append("\n\n")
            elif node.contents is not None:
                out.append(_render(node.contents))
    return "".join(out)


_WS = re.compile(r"[ \t ]+")
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")
_MAGIC = re.compile(r"__[A-Z]+__")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?)])")
_EMPTY_PARENS = re.compile(r"\(\s*[;,]?\s*\)")


def _clean_line(s: str) -> str:
    s = _HTML.sub("", s)
    s = _MAGIC.sub("", s)
    s = s.replace("'''", "").replace("''", "")
    s = _WS.sub(" ", s).strip()
    s = _EMPTY_PARENS.sub("", s)
    s = _SPACE_PUNCT.sub(r"\1", s)
    return _WS.sub(" ", s).strip()


def _paragraphs(text: str) -> list[str]:
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        current: list[str] = []
        for line in block.split("\n"):
            if line.startswith(LIST_MARK) or line.lstrip().startswith(LIST_MARK):
                if current:
                    paras.append(" ".join(current))
                    current = []
                item = _clean_line(line.replace(LIST_MARK, " "))
                if item:
                    paras.append(item)
            else:
                cl = _clean_line(line.replace(LIST_MARK, " "))
                if cl:
                    current.append(cl)
        if current:
            paras.append(" ".join(current))
    return [p for p in (_clean_line(p) for p in paras) if len(p) >= 3 and re.search(r"\w", p)]


def extract_sections(wikitext: str) -> list[dict]:
    """Return [{heading, level, paragraphs}] for visible article prose."""
    code = mwparserfromhell.parse(wikitext or "")
    sections = code.get_sections(flat=True, include_lead=True)
    result: list[dict] = []
    skip_level: int | None = None
    for i, sec in enumerate(sections):
        heading, level = "", 1
        nodes = sec.nodes
        if nodes and isinstance(nodes[0], Heading):
            heading = _clean_line(_render(nodes[0].title))
            level = nodes[0].level
        elif i > 0:
            continue
        if skip_level is not None:
            if level > skip_level:
                continue
            skip_level = None
        if heading.lower() in SKIP_SECTIONS:
            skip_level = level
            continue
        body = mwparserfromhell.parse("".join(str(n) for n in (nodes[1:] if heading or level > 1 else nodes)))
        paras = _paragraphs(_render(body))
        if paras:
            result.append({"heading": heading, "level": level, "paragraphs": paras})
    return result


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    text: str
    text_hash: str
    token_estimate: int


def _words(s: str) -> int:
    return len(s.split())


def _split_long(paragraph: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", paragraph)
    pieces, cur = [], []
    for s in sentences:
        if cur and _words(" ".join(cur + [s])) > TARGET_WORDS:
            pieces.append(" ".join(cur))
            cur = []
        cur.append(s)
    if cur:
        pieces.append(" ".join(cur))
    out = []
    for p in pieces:  # hard split pathological sentence-less text
        w = p.split()
        while len(w) > MAX_WORDS:
            out.append(" ".join(w[:TARGET_WORDS]))
            w = w[TARGET_WORDS:]
        out.append(" ".join(w))
    return out


def chunk_sections(sections: list[dict]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sec in sections:
        units: list[str] = []
        for p in sec["paragraphs"]:
            units.extend(_split_long(p) if _words(p) > MAX_WORDS else [p])
        cur: list[str] = []
        for u in units:
            if cur and _words("\n".join(cur + [u])) > MAX_WORDS and _words("\n".join(cur)) >= 1:
                chunks.append(_mk(len(chunks), sec["heading"], cur))
                cur = []
            cur.append(u)
            if _words("\n".join(cur)) >= TARGET_WORDS:
                chunks.append(_mk(len(chunks), sec["heading"], cur))
                cur = []
        if cur:
            chunks.append(_mk(len(chunks), sec["heading"], cur))
    return chunks


def embedding_input(heading: str, text: str) -> str:
    """What is embedded: the section heading carries topic context (e.g. 'Unit 3')."""
    return f"{heading}: {text}" if heading else text


def _mk(ordinal: int, heading: str, parts: list[str]) -> Chunk:
    text = "\n".join(parts)
    # Dedup key covers exactly what is embedded (heading + normalized text).
    return Chunk(ordinal=ordinal, heading=heading, text=text,
                 text_hash=hashlib.sha256(embedding_input(heading, text).encode("utf-8")).hexdigest(),
                 token_estimate=int(round(_words(text) * 1.33)))
