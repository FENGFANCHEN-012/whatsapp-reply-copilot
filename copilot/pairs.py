"""Turn a chat's message stream into (what they said -> what I replied) pairs.

Both importers (.txt export and msgstore.db) produce the same `Message` list, so the
pairing rules live here once.

A pair is:
    context  up to CONTEXT_MSGS earlier messages, for the LLM to read the thread
    in       their latest burst (one or more consecutive messages) -- what we search on
    out      my burst that answered it -- the style example

Rules, and why:
  * Bursts on BOTH sides are merged. People split one thought across 3 messages;
    pairing my answer with only their last fragment files the answer under the
    wrong question.
  * If I reply more than REPLY_GAP later, it is not a reply to that burst -- it is
    a new conversation that happens to follow it. Skipped.
  * A silence of CONVERSATION_GAP resets the context, so a thread from last week
    doesn't leak into today's suggestion.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

CONTEXT_MSGS = 8
REPLY_GAP_S = 24 * 3600
CONVERSATION_GAP_S = 6 * 3600
MERGE_MAX_CHARS = 600

# Lines that are WhatsApp placeholders, not something anyone typed.
_SKIP = re.compile(
    r"^(<media omitted>|<attached: .*>|(image|video|audio|sticker|gif|document) omitted"
    r"|this message was deleted|you deleted this message|null|missed (voice|video) call"
    r"|waiting for this message.*|<this message was edited>)$",
    re.IGNORECASE,
)


@dataclass
class Message:
    chat: str          # anything stable per chat; hashed before it is stored
    me: bool
    text: str
    ts: float          # unix seconds; 0 when unknown


def clean(text: str) -> str | None:
    t = (text or "").replace("‎", "").replace("‏", "").strip()
    t = t.replace("<This message was edited>", "").strip()
    if not t or _SKIP.match(t):
        return None
    return t


def chat_id(raw: str) -> str:
    # sha1, not the name or number: a stable grouping key that never puts a
    # contact's identity into the corpus file.
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_pairs(messages: Iterable[Message]) -> list[dict]:
    pairs: list[dict] = []
    by_chat: dict[str, list[Message]] = {}
    for m in messages:
        by_chat.setdefault(m.chat, []).append(m)

    for raw_chat, msgs in by_chat.items():
        cid = chat_id(raw_chat)
        history: list[dict] = []   # cleaned thread so far, for context
        theirs: list[str] = []     # their current burst
        mine: list[str] = []       # my burst answering it
        their_ts = my_ts = last_ts = 0.0
        burst_start = 0            # where their burst begins in `history`

        def flush():
            if theirs and mine and (not their_ts or not my_ts or my_ts - their_ts <= REPLY_GAP_S):
                ctx = history[:burst_start][-CONTEXT_MSGS:]
                pairs.append({
                    "chat": cid,
                    "at": my_ts,
                    "date": datetime.fromtimestamp(my_ts).strftime("%Y-%m-%d") if my_ts else "",
                    "context": ctx,
                    "in": "\n".join(theirs),
                    "out": "\n".join(mine),
                    "source": "import",
                })

        for m in msgs:
            t = clean(m.text)
            if t is None:
                continue
            late_answer = m.me and theirs and not mine   # "sorry just saw this" -- still an answer
            if last_ts and m.ts and m.ts - last_ts > CONVERSATION_GAP_S and not late_answer:
                flush()
                history, theirs, mine = [], [], []
            last_ts = m.ts or last_ts

            if not m.me:
                if mine:                       # they spoke again: my answer is complete
                    flush()
                    theirs, mine = [], []
                if not theirs:
                    burst_start = len(history)
                if len("\n".join(theirs + [t])) <= MERGE_MAX_CHARS:
                    theirs.append(t)
                    their_ts = m.ts
            else:
                if not theirs:                 # I spoke first: nothing to answer
                    history.append({"dir": "out", "text": t})
                    continue
                if len("\n".join(mine + [t])) <= MERGE_MAX_CHARS:
                    mine.append(t)
                    my_ts = m.ts
            history.append({"dir": "out" if m.me else "in", "text": t})
        flush()

    pairs.sort(key=lambda p: p["at"], reverse=True)
    return pairs


def write_pairs(pairs: list[dict], path: Path, append: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    return len(pairs)


def read_pairs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
