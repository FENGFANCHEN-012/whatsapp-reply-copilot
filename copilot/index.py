"""Vector index over "what they said" so we can find how I answered similar things.

Plain numpy: a personal chat history is tens of thousands of pairs at most, and a
normalised matrix + one matmul answers in milliseconds on a laptop CPU. A vector
database would add a service to run for no measurable gain at this size.

Row i of `vecs` is pair i of pairs.jsonl followed by feedback.jsonl, in that order.
`python -m copilot doctor` flags the index as stale when the counts disagree.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Protocol

import numpy as np

from . import config
from .pairs import read_pairs


class Embedder(Protocol):
    def __call__(self, texts: list[str]) -> np.ndarray: ...


def query_text(p: dict) -> str:
    # Embed their burst plus the last bit of context: "yes" means nothing alone,
    # "yes" after "dinner tomorrow?" is searchable.
    ctx = " ".join(m["text"] for m in (p.get("context") or [])[-2:])
    return (ctx + " \n" + p["in"]).strip()[-800:]


def fastembed_embedder(model: str) -> Embedder:
    from fastembed import TextEmbedding   # imported lazily: first use downloads the model
    te = TextEmbedding(model_name=model)

    def embed(texts: list[str]) -> np.ndarray:
        return np.asarray(list(te.embed(texts, batch_size=64)), dtype=np.float32)
    return embed


def hash_embedder(dim: int = 256) -> Embedder:
    """Dependency-free bag-of-words hashing. For tests and offline demos only."""
    def embed(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().split():
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1
        return out
    return embed


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.where(n == 0, 1, n)


class Index:
    def __init__(self, pairs: list[dict], vecs: np.ndarray, embed: Embedder):
        self.pairs, self.vecs, self.embed = pairs, vecs, embed

    @classmethod
    def build(cls, pairs: list[dict], embed: Embedder, progress: Callable[[int], None] | None = None) -> "Index":
        chunks = []
        for i in range(0, len(pairs), 256):
            chunks.append(embed([query_text(p) for p in pairs[i:i + 256]]))
            if progress:
                progress(min(i + 256, len(pairs)))
        vecs = _normalise(np.vstack(chunks)) if chunks else np.zeros((0, 1), np.float32)
        return cls(pairs, vecs, embed)

    @classmethod
    def load(cls, embed: Embedder) -> "Index":
        pairs = read_pairs(config.PAIRS_FILE) + read_pairs(config.FEEDBACK_FILE)
        with np.load(config.INDEX_FILE) as z:
            vecs = z["vecs"]
        if len(vecs) != len(pairs):
            raise RuntimeError(f"Index has {len(vecs)} rows but there are {len(pairs)} pairs. "
                               "Run: python -m copilot index")
        return cls(pairs, vecs, embed)

    def save(self) -> None:
        config.DATA.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(config.INDEX_FILE, vecs=self.vecs)

    def add(self, pair: dict) -> None:
        v = _normalise(self.embed([query_text(pair)]))
        self.vecs = v if not len(self.vecs) else np.vstack([self.vecs, v])
        self.pairs.append(pair)

    def search(self, messages: list[dict], k: int = 12, exclude_chat: str | None = None) -> list[tuple[float, dict]]:
        if not len(self.pairs):
            return []
        # The query mirrors query_text(): their latest burst plus a little context.
        theirs, i = [], len(messages) - 1
        while i >= 0 and messages[i]["dir"] == "in":
            theirs.insert(0, messages[i]["text"])
            i -= 1
        probe = {"in": "\n".join(theirs) or (messages[-1]["text"] if messages else ""),
                 "context": messages[max(0, i - 1): i + 1]}
        q = _normalise(self.embed([query_text(probe)]))[0]
        scores = self.vecs @ q
        order = np.argsort(-scores)
        out = []
        for j in order:
            p = self.pairs[j]
            if exclude_chat and p.get("chat") == exclude_chat:
                continue
            out.append((float(scores[j]), p))
            if len(out) >= k:
                break
        return out
