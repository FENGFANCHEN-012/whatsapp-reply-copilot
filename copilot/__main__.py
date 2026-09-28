"""Command line:  python -m copilot <command>

    init      create .env with a generated SERVER_TOKEN
    doctor    show what is configured and what you still need to do
    import    read WhatsApp .txt exports and/or a .crypt15 backup into data/pairs.jsonl
    index     embed the pairs so they can be searched
    serve     run the local server for the Chrome extension
    eval      measure suggestions against replies you really sent
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from . import config


def cmd_init(a):
    print(config.init_env())
    cmd_doctor(a)


def cmd_doctor(a):
    s = config.load()
    checks = config.check(s)
    for c in checks:
        mark = "OK  " if c.ok else ("MISS" if c.required else "WARN")
        print(f"[{mark}] {c.name:<20} {c.detail}")
        if not c.ok:
            print(f"       -> {c.fix}")
    need = [c for c in checks if not c.ok and c.required]
    print("\nReady." if not need else f"\n{len(need)} thing(s) left to set up (see -> lines above).")
    return 0 if not need else 1


def cmd_import(a):
    from .importers import msgstore, txt_export
    from .pairs import build_pairs, read_pairs, write_pairs

    s = config.load()
    me = a.me or s.my_name
    messages = []
    for f in a.files:
        p = Path(f)
        if not me:
            sys.exit("Set MY_NAME in .env (or pass --me) so your own messages can be recognised.")
        who = txt_export.senders(p)
        if me.casefold() not in {k.casefold() for k in who}:
            # Wrong name = every message counted as "theirs" = zero pairs, silently.
            sys.exit(f"'{me}' never speaks in {p.name}. Senders found: {', '.join(who) or 'none'}. "
                     "Fix MY_NAME in .env or pass --me.")
        msgs = txt_export.parse(p, me, a.date_order)
        print(f"{p.name}: {len(msgs)} messages")
        messages += msgs
    if a.backup:
        key = a.key
        if key == "-":
            key = getpass.getpass("64-digit backup key (hidden): ")
        msgs = msgstore.load(Path(a.backup), key)
        print(f"{Path(a.backup).name}: {len(msgs)} messages")
        messages += msgs
    if not messages:
        sys.exit("Nothing to import. Pass .txt exports and/or --backup.")

    pairs = build_pairs(messages)
    if a.append:
        old = read_pairs(config.PAIRS_FILE)
        seen = {(p["chat"], p["in"], p["out"]) for p in old}
        pairs = old + [p for p in pairs if (p["chat"], p["in"], p["out"]) not in seen]
    n = write_pairs(pairs, config.PAIRS_FILE)
    print(f"Wrote {n} reply pairs to {config.PAIRS_FILE}\nNext: python -m copilot index")


def cmd_index(a):
    from .index import Index, fastembed_embedder
    from .pairs import read_pairs

    s = config.load()
    pairs = read_pairs(config.PAIRS_FILE) + read_pairs(config.FEEDBACK_FILE)
    if not pairs:
        sys.exit("No pairs yet. Run: python -m copilot import ...")
    print(f"Embedding {len(pairs)} pairs with {s.embed_model} (first run downloads the model)...")
    idx = Index.build(pairs, fastembed_embedder(s.embed_model),
                      progress=lambda n: print(f"\r  {n}/{len(pairs)}", end="", flush=True))
    idx.save()
    print(f"\nSaved {config.INDEX_FILE}")


def cmd_serve(a):
    import uvicorn
    from .server import create_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = config.load()
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        # Your chat history is behind this server. Refuse to put it on the network.
        sys.exit(f"HOST={s.host} would expose your chat history to the network. Use 127.0.0.1.")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="warning")


def cmd_eval(a):
    from .evaluate import run
    from .index import fastembed_embedder
    from .pairs import read_pairs

    s = config.load()
    pairs = read_pairs(config.PAIRS_FILE)
    out = run(s, pairs, fastembed_embedder(s.embed_model), holdout=a.holdout, limit=a.limit,
              use_llm=not a.no_llm, delay=a.delay, show=a.show)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="copilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("import", help="import chats")
    p.add_argument("files", nargs="*", help="WhatsApp 'Export chat' .txt files")
    p.add_argument("--me", help="your name in the exports (default: MY_NAME)")
    p.add_argument("--date-order", choices=["auto", "dmy", "mdy"], default="auto")
    p.add_argument("--backup", help="msgstore.db.crypt15 or a decrypted msgstore.db")
    p.add_argument("--key", help="64-digit hex backup key, or '-' to type it hidden")
    p.add_argument("--append", action="store_true", help="add to existing pairs instead of replacing")
    p.set_defaults(fn=cmd_import)

    sub.add_parser("index").set_defaults(fn=cmd_index)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)

    p = sub.add_parser("eval", help="score suggestions against your real replies")
    p.add_argument("--holdout", type=float, default=0.1)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--no-llm", action="store_true", help="retrieval baseline only (no API calls)")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between LLM calls")
    p.add_argument("--show", type=int, default=5, help="print this many cases")
    p.set_defaults(fn=cmd_eval)

    a = ap.parse_args(argv)
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
