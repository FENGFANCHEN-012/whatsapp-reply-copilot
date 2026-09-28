"""Settings from .env, plus the checks behind `python -m copilot doctor`.

This project ships as a framework: no chats, no keys. Every user brings their own,
so "what is still missing" has to be answerable at any moment -- by the CLI, by the
server at startup, and by the extension popup (via /health). All three read the
same `check()` list so they can never disagree.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
DATA = ROOT / "data"
PAIRS_FILE = DATA / "pairs.jsonl"
FEEDBACK_FILE = DATA / "feedback.jsonl"
INDEX_FILE = DATA / "index.npz"


@dataclass
class Settings:
    my_name: str = ""
    server_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    providers: list[str] = field(default_factory=lambda: ["groq", "gemini", "ollama"])
    groq_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    examples_k: int = 12


def load() -> Settings:
    load_dotenv(ENV_FILE, override=False)
    g = lambda k, d="": (os.getenv(k) or d).strip()
    return Settings(
        my_name=g("MY_NAME"),
        server_token=g("SERVER_TOKEN"),
        host=g("HOST", "127.0.0.1"),
        port=int(g("PORT", "8765")),
        providers=[p.strip().lower() for p in g("LLM_PROVIDERS", "groq,gemini,ollama").split(",") if p.strip()],
        groq_key=g("GROQ_API_KEY"),
        groq_model=g("GROQ_MODEL", "llama-3.3-70b-versatile"),
        gemini_key=g("GEMINI_API_KEY"),
        gemini_model=g("GEMINI_MODEL", "gemini-2.5-flash"),
        ollama_url=g("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=g("OLLAMA_MODEL", "llama3.2:3b"),
        embed_model=g("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        examples_k=int(g("EXAMPLES_K", "12")),
    )


def init_env() -> str:
    """Create .env from the template with a fresh SERVER_TOKEN. Never overwrites."""
    if ENV_FILE.exists():
        return f".env already exists at {ENV_FILE} -- left untouched."
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = text.replace("SERVER_TOKEN=\n", f"SERVER_TOKEN={secrets.token_urlsafe(24)}\n", 1)
    ENV_FILE.write_text(text, encoding="utf-8")
    return f"Created {ENV_FILE}. Now fill in MY_NAME and at least one LLM provider."


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True     # False = a warning; the app still runs without it


def _ollama_up(url: str) -> bool:
    import httpx
    try:
        return httpx.get(f"{url}/api/tags", timeout=1.5).status_code == 200
    except Exception:
        return False


def _count_lines(p: Path) -> int:
    if not p.exists():
        return 0
    with p.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def check(s: Settings | None = None, probe_ollama: bool = True) -> list[Check]:
    s = s or load()
    out = [
        Check(".env file", ENV_FILE.exists(), str(ENV_FILE),
              "Run: python -m copilot init"),
        Check("MY_NAME", bool(s.my_name), s.my_name or "not set",
              "Set MY_NAME in .env to your name as shown in your WhatsApp exports"),
        Check("SERVER_TOKEN", len(s.server_token) >= 16,
              "set" if s.server_token else "not set",
              "Run `python -m copilot init`, or set a random 16+ char SERVER_TOKEN in .env"),
    ]

    llm = []
    for p in s.providers:
        if p == "groq":
            llm.append(("groq", bool(s.groq_key)))
        elif p == "gemini":
            llm.append(("gemini", bool(s.gemini_key)))
        elif p == "ollama":
            llm.append(("ollama", _ollama_up(s.ollama_url) if probe_ollama else False))
    ready = [n for n, ok in llm if ok]
    out.append(Check(
        "LLM provider", bool(ready),
        ("ready: " + ", ".join(ready)) if ready else "none reachable",
        "Set GROQ_API_KEY and/or GEMINI_API_KEY in .env, or start Ollama. "
        "Without one, suggestions fall back to your closest past replies only.",
        required=False,
    ))

    n_pairs = _count_lines(PAIRS_FILE)
    out.append(Check(
        "Chat data imported", n_pairs > 0, f"{n_pairs} reply pairs in {PAIRS_FILE.name}",
        "Import your chats: python -m copilot import <export.txt ...>  "
        "or  python -m copilot import --backup msgstore.db.crypt15 --key <key>",
    ))

    idx_ok, idx_detail = False, "not built"
    if INDEX_FILE.exists():
        import numpy as np
        with np.load(INDEX_FILE) as z:
            rows = int(z["vecs"].shape[0])
        total = n_pairs + _count_lines(FEEDBACK_FILE)
        idx_ok = rows == total and rows > 0
        idx_detail = f"{rows} rows" + ("" if idx_ok else f" but {total} pairs -- stale")
    out.append(Check("Search index", idx_ok, idx_detail,
                     "Run: python -m copilot index"))
    return out


def problems(checks: list[Check]) -> list[str]:
    """One human-readable line per unmet check, for startup logs and /health."""
    return [f"{c.name}: {c.detail}. {c.fix}" for c in checks if not c.ok]
