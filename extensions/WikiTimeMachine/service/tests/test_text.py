from wtm.diff import diff_sections, word_diff
from wtm.normalize import chunk_sections, extract_sections

WIKITEXT = """{{Short description|Nuclear power plant in Finland}}
{{Infobox power station
| name = Olkiluoto
| status = O
}}
The '''Olkiluoto Nuclear Power Plant''' is on [[Olkiluoto|Olkiluoto Island]] in [[Eurajoki]].<ref>{{cite web|url=http://x|title=Y}}</ref>
Unit 3 has a capacity of {{convert|1600|MW}} and is '''not''' yet operational.<!-- hidden comment -->

[[File:OL3.jpg|thumb|A caption that should vanish]]

== Unit 3 ==
{{Main|Olkiluoto 3}}
Construction began in 2005. {{As of|2021|12}}, the reactor reached criticality on 21 December 2021.
* First item with [[link]]
* Second item

{| class="wikitable"
! A !! B
|-
| 1 || 2
|}

== References ==
{{Reflist}}
[[Category:Nuclear power stations in Finland]]
"""


def test_extract_sections_keeps_prose_numbers_negation():
    s = extract_sections(WIKITEXT)
    assert [x["heading"] for x in s] == ["", "Unit 3"]
    lead = " ".join(s[0]["paragraphs"])
    assert "Olkiluoto Nuclear Power Plant is on Olkiluoto Island in Eurajoki." in lead
    assert "1600 MW" in lead and "not yet operational" in lead
    assert "cite" not in lead and "hidden comment" not in lead and "caption" not in lead
    unit3 = s[1]["paragraphs"]
    assert "As of December 2021, the reactor reached criticality on 21 December 2021." in unit3[0]
    assert unit3[1:] == ["First item with link", "Second item"]
    assert all("Category" not in p and "wikitable" not in p for p in unit3)


def test_chunks_deterministic_and_bounded():
    long = " ".join(f"Sentence number {i} talks about reactor {i}." for i in range(300))
    secs = [{"heading": "H", "level": 2, "paragraphs": [long, "Short tail."]}]
    a, b = chunk_sections(secs), chunk_sections(secs)
    assert a == b
    assert all(c.token_estimate <= 360 for c in a)
    assert len({c.text_hash for c in a}) == len(a)


def test_diff_identical_and_word_level():
    s = extract_sections(WIKITEXT)
    assert diff_sections(s, s)["identical"]
    s2 = extract_sections(WIKITEXT.replace("is '''not''' yet operational", "is operational"))
    d = diff_sections(s, s2)
    assert not d["identical"]
    para = [p for p in d["sections"][0]["paragraphs"] if p["status"] == "changed"][0]
    assert {"op": "del", "text": "not yet "} in para["words"] or any(w["op"] == "del" and "not" in w["text"] for w in para["words"])


def test_diff_added_and_removed_sections():
    a = [{"heading": "", "level": 1, "paragraphs": ["Lead."]}, {"heading": "Old", "level": 2, "paragraphs": ["Gone."]}]
    b = [{"heading": "", "level": 1, "paragraphs": ["Lead."]}, {"heading": "New", "level": 2, "paragraphs": ["Fresh."]}]
    d = diff_sections(a, b)
    assert [s["status"] for s in d["sections"]] == ["same", "removed", "added"]
    assert d["stats"]["sections_added"] == 1 and d["stats"]["sections_removed"] == 1


def test_word_diff_roundtrip():
    ops = word_diff("The unit is under construction.", "The unit is in commercial operation.")
    assert "".join(o["text"] for o in ops if o["op"] != "ins") == "The unit is under construction."
    assert "".join(o["text"] for o in ops if o["op"] != "del") == "The unit is in commercial operation."
