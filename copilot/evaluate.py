"""Offline evaluation: would the drafts have matched what I really sent?

Time-based split -- the newest HOLDOUT share of pairs is the test set, everything
older is the index. A random split would leak: the same conversation would sit on
both sides and retrieval would "find" the answer it is being tested on.

For each test pair we score, by embedding cosine similarity to what I actually sent:
    retrieval@1   my single closest past reply, used verbatim (the no-LLM baseline)
    retrieval@k   best of the k retrieved replies (the ceiling a perfect re-ranker could reach)
    llm best-of-3 best of the three generated drafts
Similarity is a proxy, not a judgement of quality -- read a sample of cases too
(`--show`), which is what the per-case output is for.
"""
from __future__ import annotations

import time

import numpy as np

from .config import Settings
from .index import Index
from .suggest import suggest


def _sim(embed, a: str, b_list: list[str]) -> float:
    v = embed([a] + b_list)
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    return float(np.max(v[1:] @ v[0])) if b_list else 0.0


def run(s: Settings, pairs: list[dict], embed, holdout: float = 0.1, limit: int = 50,
        use_llm: bool = True, delay: float = 0.0, show: int = 0) -> dict:
    ordered = sorted(pairs, key=lambda p: p.get("at") or 0)
    cut = max(1, int(len(ordered) * (1 - holdout)))
    train, test = ordered[:cut], ordered[cut:][-limit:]
    if not test:
        raise SystemExit("Not enough pairs to hold any out.")
    index = Index.build(train, embed)

    rows = []
    for p in test:
        thread = (p.get("context") or []) + [{"dir": "in", "text": p["in"]}]
        hits = index.search(thread, k=s.examples_k)
        outs = [h["out"] for _, h in hits]
        row = {"in": p["in"], "gold": p["out"],
               "r1": _sim(embed, p["out"], outs[:1]),
               "rk": _sim(embed, p["out"], outs)}
        if use_llm:
            res = suggest(s, index, thread)
            drafts = [x["text"] for x in res["suggestions"]]
            row.update(llm=_sim(embed, p["out"], drafts), provider=res["provider"], drafts=drafts)
            if delay:
                time.sleep(delay)     # free tiers rate-limit; spread the calls out
        rows.append(row)

    summary = {"train": len(train), "test": len(test),
               "retrieval@1": round(float(np.mean([r["r1"] for r in rows])), 3),
               f"retrieval@{s.examples_k}": round(float(np.mean([r["rk"] for r in rows])), 3)}
    if use_llm:
        llm_rows = [r for r in rows if r["provider"] not in (None, "retrieval")]
        summary["llm_best_of_3"] = round(float(np.mean([r["llm"] for r in llm_rows])), 3) if llm_rows else None
        summary["llm_answered"] = f"{len(llm_rows)}/{len(rows)}"
    summary["cases"] = rows[:show]
    return summary
