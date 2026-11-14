"""OpenAI-compatible client wire format (no network): request shape, auth header, <think> stripping."""
import json

import httpx

from wtm import answer
from wtm.answer import OpenAICompatClient, synthesize

EV_A = [{"id": "A1", "revision_id": 1, "url": "https://en.wikipedia.org/w/index.php?oldid=1", "section": "", "text": "Under construction."}]
EV_B = [{"id": "B1", "revision_id": 3, "url": "https://en.wikipedia.org/w/index.php?oldid=3", "section": "Unit 3", "text": "In operation."}]
SIDE = {"status": "ok", "revision_id": 1, "source_timestamp": "x", "requested_at": "y"}


def test_client_request_and_reasoning_output(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers, timeout=timeout)
        content = "<think>reasoning...</think>```json\n" + __import__("json").dumps({
            "before": {"status": "answered", "statements": [{"text": "Under construction.", "snippet_ids": ["A1"]}]},
            "after": {"status": "answered", "statements": [{"text": "Operating.", "snippet_ids": ["B1"]}]},
            "change": None}) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(answer.httpx, "post", fake_post)
    client = OpenAICompatClient("https://integrate.api.nvidia.com/v1/", "nvidia/nemotron", "secret", timeout=5)
    out = synthesize("What changed?", SIDE, {**SIDE, "revision_id": 3}, EV_A, EV_B, client)
    assert out["status"] == "ok"
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer secret" and seen["timeout"] == 5
    assert seen["body"]["model"] == "nvidia/nemotron" and seen["body"]["temperature"] == 0.0
    user = seen["body"]["messages"][1]["content"]
    assert "[A1]" in user and "[B1]" in user and user.index("[A1]") < user.index("LATER REVISION")
    assert out["answer"]["after"]["statements"][0]["citations"][0]["url"].endswith("oldid=3")


def test_http_error_is_model_unavailable(monkeypatch):
    def fake_post(url, **kw):
        return httpx.Response(503, request=httpx.Request("POST", url))
    monkeypatch.setattr(answer.httpx, "post", fake_post)
    out = synthesize("q?", SIDE, SIDE, EV_A, EV_B, OpenAICompatClient("http://x/v1", "m"))
    assert out["status"] == "model_unavailable"


def _ok_content():
    return json.dumps({"before": {"status": "answered", "statements": [{"text": "x", "snippet_ids": ["A1"]}]},
                       "after": {"status": "insufficient_evidence", "statements": []}, "change": None})


def test_structured_schema_sent_and_trailing_text_ignored(monkeypatch):
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": _ok_content() + "\n{\"extra\": 1} trailing"}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(answer.httpx, "post", fake_post)
    out = synthesize("q?", SIDE, SIDE, EV_A, EV_B, OpenAICompatClient("http://x/v1", "m"))
    assert out["status"] == "ok"
    schema = bodies[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["before"]["properties"]["statements"]["items"]["properties"]["snippet_ids"]["items"]["enum"] == ["A1"]
    assert schema["properties"]["after"]["properties"]["statements"]["items"]["properties"]["snippet_ids"]["items"]["enum"] == ["B1"]


def test_structured_output_unsupported_falls_back(monkeypatch):
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(dict(json))
        if "response_format" in json:
            return httpx.Response(400, json={"error": "unsupported"}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": _ok_content()}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(answer.httpx, "post", fake_post)
    out = synthesize("q?", SIDE, SIDE, EV_A, EV_B, OpenAICompatClient("http://x/v1", "m"))
    assert out["status"] == "ok" and len(bodies) == 2 and "response_format" not in bodies[1]


def test_empty_content_rejected(monkeypatch):
    monkeypatch.setattr(answer.httpx, "post", lambda url, **kw: httpx.Response(
        200, json={"choices": [{"message": {"content": None, "reasoning": "..."}, "finish_reason": "length"}]},
        request=httpx.Request("POST", url)))
    out = synthesize("q?", SIDE, SIDE, EV_A, EV_B, OpenAICompatClient("http://x/v1", "m"))
    assert out["status"] == "model_output_rejected" and "length" in out["error"]
