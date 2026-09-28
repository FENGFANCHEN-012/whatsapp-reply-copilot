"""Local HTTP server the Chrome extension talks to.

    GET  /health    what is configured / missing (the popup shows this)
    POST /suggest   {messages:[{dir:"in"|"out", text}]} -> 3 drafts
    POST /feedback  what I actually sent after a suggestion -> learned immediately

Binds to 127.0.0.1 only. Every request needs the X-Copilot-Token header: any web
page in the browser can send requests to localhost, and without the token one
could read drafts built from your chat history. No CORS headers are sent on
purpose -- the extension calls from its background worker (host permission), so
no web origin ever needs to read a response.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import config
from .index import Index, fastembed_embedder
from .pairs import write_pairs
from .suggest import suggest as run_suggest

log = logging.getLogger("copilot")


class Msg(BaseModel):
    dir: str
    text: str


class SuggestReq(BaseModel):
    messages: list[Msg]


class FeedbackReq(BaseModel):
    messages: list[Msg]           # the thread as it was when suggestions were made
    suggestions: list[str] = []
    chosen: str | None = None     # the suggestion clicked, if any
    sent: str                     # what actually went out, after any edits


def create_app(settings: config.Settings | None = None, index: Index | None = None,
               load_index: bool = True) -> FastAPI:
    s = settings or config.load()
    state = {"index": index}
    lock = threading.Lock()

    if state["index"] is None and load_index and config.INDEX_FILE.exists():
        try:
            state["index"] = Index.load(fastembed_embedder(s.embed_model))
            log.info("index loaded: %d pairs", len(state["index"].pairs))
        except Exception as e:
            log.warning("index not loaded: %s", e)

    for line in config.problems(config.check(s)):
        log.warning("SETUP NEEDED - %s", line)

    def auth(x_copilot_token: str = Header(default="")):
        if not s.server_token:
            raise HTTPException(503, "SERVER_TOKEN is not set. Run: python -m copilot init")
        if not secrets.compare_digest(x_copilot_token, s.server_token):
            raise HTTPException(401, "bad or missing X-Copilot-Token")

    app = FastAPI(title="Reply Copilot", docs_url=None, redoc_url=None)

    @app.get("/health", dependencies=[Depends(auth)])
    def health():
        checks = config.check(s)
        idx = state["index"]
        return {
            "ok": all(c.ok for c in checks if c.required),
            "pairs": len(idx.pairs) if idx else 0,
            "checks": [c.__dict__ for c in checks],
            "problems": config.problems(checks),
        }

    @app.post("/suggest", dependencies=[Depends(auth)])
    def suggest(req: SuggestReq):
        t0 = time.perf_counter()
        out = run_suggest(s, state["index"], [m.model_dump() for m in req.messages])
        out["ms"] = int((time.perf_counter() - t0) * 1000)
        if state["index"] is None:
            out["warning"] = "No chat history indexed yet, so drafts won't sound like you. " \
                             "Run: python -m copilot import ... then python -m copilot index"
        return out

    @app.post("/feedback", dependencies=[Depends(auth)])
    def feedback(req: FeedbackReq):
        msgs = [m.model_dump() for m in req.messages if m.text.strip()]
        sent = req.sent.strip()
        if not msgs or not sent:
            raise HTTPException(400, "messages and sent are required")
        theirs, i = [], len(msgs) - 1
        while i >= 0 and msgs[i]["dir"] == "in":
            theirs.insert(0, msgs[i]["text"])
            i -= 1
        if not theirs:
            return {"learned": False, "reason": "last message was mine; nothing to answer"}
        pair = {
            "chat": "live", "at": time.time(), "date": time.strftime("%Y-%m-%d"),
            "context": msgs[: i + 1][-8:], "in": "\n".join(theirs), "out": sent,
            "source": "feedback",
            # Kept for evaluation: how often a draft goes out untouched vs edited.
            "chosen": req.chosen, "edited": req.chosen is not None and req.chosen.strip() != sent,
            "suggestions": req.suggestions,
        }
        with lock:
            write_pairs([pair], config.FEEDBACK_FILE, append=True)
            if state["index"] is not None:
                state["index"].add(pair)
                state["index"].save()
        return {"learned": True, "indexed": state["index"] is not None}

    return app
