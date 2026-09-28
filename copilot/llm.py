"""LLM providers behind one call, tried in LLM_PROVIDERS order.

Each provider returns raw text or raises. The chain moves on after any failure
(no key, timeout, rate limit, bad JSON) and records why, so /suggest can report
which provider answered and the popup can show why the others didn't.

Free tiers rate-limit, so the fallback is the normal path, not an edge case:
Groq (fastest) -> Gemini (generous free quota) -> Ollama (local, slow on CPU, never
rate-limited).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .config import Settings

TIMEOUT = {"groq": 12.0, "gemini": 20.0, "ollama": 60.0}


class ProviderSkipped(Exception):
    """Not configured -- not an error worth a warning."""


def _groq(s: Settings, system: str, user: str) -> str:
    if not s.groq_key:
        raise ProviderSkipped("GROQ_API_KEY not set")
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {s.groq_key}"},
        json={"model": s.groq_model, "temperature": 0.7,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=TIMEOUT["groq"],
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(s: Settings, system: str, user: str) -> str:
    if not s.gemini_key:
        raise ProviderSkipped("GEMINI_API_KEY not set")
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent",
        # Header, not ?key=: keeps the key out of any logged URL.
        headers={"x-goog-api-key": s.gemini_key},
        json={"systemInstruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": [{"text": user}]}],
              "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"}},
        timeout=TIMEOUT["gemini"],
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _ollama(s: Settings, system: str, user: str) -> str:
    try:
        r = httpx.post(
            f"{s.ollama_url}/api/chat",
            json={"model": s.ollama_model, "stream": False, "format": "json",
                  "options": {"temperature": 0.7},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=TIMEOUT["ollama"],
        )
    except httpx.ConnectError:
        raise ProviderSkipped(f"Ollama not running at {s.ollama_url}")
    r.raise_for_status()
    return r.json()["message"]["content"]


PROVIDERS = {"groq": _groq, "gemini": _gemini, "ollama": _ollama}


@dataclass
class Result:
    provider: str | None = None
    text: str = ""
    attempts: list[dict] = field(default_factory=list)


def complete(s: Settings, system: str, user: str, validate=None) -> Result:
    """Try each provider until one returns text that `validate` accepts.

    `validate(text)` raises on output we can't use, so a provider that answers
    with garbage is treated like one that errored and the next one gets a turn.
    """
    res = Result()
    for name in s.providers:
        fn = PROVIDERS.get(name)
        if fn is None:
            res.attempts.append({"provider": name, "ok": False, "error": "unknown provider"})
            continue
        t0 = time.perf_counter()
        try:
            text = fn(s, system, user)
            if validate:
                validate(text)
        except ProviderSkipped as e:
            res.attempts.append({"provider": name, "ok": False, "skipped": True, "error": str(e)})
            continue
        except httpx.HTTPStatusError as e:
            # 429 = free-tier rate limit: expected, move on quietly.
            res.attempts.append({"provider": name, "ok": False,
                                 "error": f"HTTP {e.response.status_code}"})
            continue
        except Exception as e:
            res.attempts.append({"provider": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        res.attempts.append({"provider": name, "ok": True, "ms": int((time.perf_counter() - t0) * 1000)})
        res.provider, res.text = name, text
        return res
    return res
