import json

import pytest
from fastapi.testclient import TestClient

from conftest import EXAMPLES
from copilot import config, llm
from copilot.importers import txt_export
from copilot.index import Index, hash_embedder
from copilot.pairs import build_pairs, read_pairs
from copilot.server import create_app
from copilot.suggest import parse_suggestions, suggest

TOKEN = "t" * 24


def settings(**kw):
    base = dict(my_name="Sam", server_token=TOKEN, providers=["groq", "gemini", "ollama"])
    base.update(kw)
    return config.Settings(**base)


@pytest.fixture
def index():
    msgs = []
    for f in EXAMPLES.glob("*.txt"):
        msgs += txt_export.parse(f, "Sam")
    return Index.build(build_pairs(msgs), hash_embedder())


def fake(name, reply=None, exc=None):
    def fn(s, system, user):
        if exc:
            raise exc
        return reply
    return fn


def test_parse_suggestions_tolerates_wrapping():
    assert parse_suggestions('Sure!\n{"suggestions": ["a", "b", "a", "c", "d"]}') == ["a", "b", "c"]
    with pytest.raises(Exception):
        parse_suggestions('{"suggestions": []}')


def test_falls_through_to_next_provider(monkeypatch, index):
    monkeypatch.setitem(llm.PROVIDERS, "groq", fake("groq", exc=RuntimeError("429")))
    monkeypatch.setitem(llm.PROVIDERS, "gemini", fake("gemini", reply="not json"))
    monkeypatch.setitem(llm.PROVIDERS, "ollama", fake("ollama", reply='{"suggestions":["ya sure"]}'))
    out = suggest(settings(), index, [{"dir": "in", "text": "lunch tomorrow?"}])
    assert out["provider"] == "ollama"
    assert out["suggestions"] == [{"text": "ya sure", "source": "ollama"}]
    assert [a["ok"] for a in out["attempts"]] == [False, False, True]


def test_no_llm_returns_my_own_past_replies(monkeypatch, index):
    for name in ("groq", "gemini", "ollama"):
        monkeypatch.setitem(llm.PROVIDERS, name, fake(name, exc=llm.ProviderSkipped("off")))
    out = suggest(settings(), index, [{"dir": "in", "text": "are you free for lunch tomorrow?"}])
    assert out["provider"] == "retrieval" and out["suggestions"]
    my_replies = {p["out"] for p in index.pairs}
    assert all(s["text"] in my_replies for s in out["suggestions"])


def test_server_requires_token(tmp_data, index):
    c = TestClient(create_app(settings(), index=index, load_index=False))
    assert c.post("/suggest", json={"messages": []}).status_code == 401
    assert c.post("/suggest", json={"messages": []}, headers={"X-Copilot-Token": "nope"}).status_code == 401


def test_server_refuses_when_token_unset(tmp_data, index):
    c = TestClient(create_app(settings(server_token=""), index=index, load_index=False))
    assert c.get("/health", headers={"X-Copilot-Token": ""}).status_code == 503


def test_health_lists_what_is_missing(tmp_data, index):
    c = TestClient(create_app(settings(), index=index, load_index=False))
    h = c.get("/health", headers={"X-Copilot-Token": TOKEN}).json()
    names = {x["name"] for x in h["checks"] if not x["ok"]}
    assert "Chat data imported" in names and h["problems"]


def test_feedback_is_learned_immediately(tmp_data, monkeypatch, index):
    monkeypatch.setitem(llm.PROVIDERS, "groq", fake("groq", reply='{"suggestions":["x"]}'))
    c = TestClient(create_app(settings(), index=index, load_index=False))
    n = len(index.pairs)
    r = c.post("/feedback", headers={"X-Copilot-Token": TOKEN}, json={
        "messages": [{"dir": "out", "text": "hey"}, {"dir": "in", "text": "wanna play badminton sat?"}],
        "suggestions": ["sure!", "can't this week"], "chosen": "sure!", "sent": "sure! what time",
    }).json()
    assert r == {"learned": True, "indexed": True}
    assert len(index.pairs) == n + 1
    saved = read_pairs(tmp_data / "feedback.jsonl")
    assert saved[0]["out"] == "sure! what time" and saved[0]["edited"] is True
    assert saved[0]["context"] == [{"dir": "out", "text": "hey"}]
    # And the next search can find it.
    hits = index.search([{"dir": "in", "text": "badminton on sat?"}], k=1)
    assert hits[0][1]["out"] == "sure! what time"
