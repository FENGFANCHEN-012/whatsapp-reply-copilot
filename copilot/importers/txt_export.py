"""Parse WhatsApp's "Export chat" .txt (Android and iOS formats).

    Android:  12/31/23, 9:15 PM - Alex: see you there
              31/12/2023, 21:15 - Alex: see you there
    iOS:      [31/12/2023, 21:15:03] Alex: see you there

Lines that don't start with a timestamp continue the previous message (multi-line
messages). Timestamp lines without "Name: " are system notices and are dropped.

The date order (day-first vs month-first) depends on the phone's locale and is not
written in the file, so `auto` scans every date: a first field > 12 means day-first,
a second field > 12 means month-first. Ambiguous files default to day-first.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..pairs import Message

_ANDROID = re.compile(
    r"^(?P<d>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<t>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap]\.?[Mm]\.?)?)\s+-\s+(?P<rest>.*)$"
)
_IOS = re.compile(
    r"^\[(?P<d>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<t>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap]\.?[Mm]\.?)?)\]\s+(?P<rest>.*)$"
)


def _norm(line: str) -> str:
    # iOS prefixes lines with U+200E; newer Android puts U+202F (narrow nbsp) before AM/PM.
    return line.replace("‎", "").replace(" ", " ").replace(" ", " ").rstrip("\r\n")


def _match(line: str):
    return _ANDROID.match(line) or _IOS.match(line)


def detect_date_order(lines: list[str]) -> str:
    for line in lines:
        m = _match(_norm(line))
        if not m:
            continue
        a, b, _ = m.group("d").split("/")
        if int(a) > 12:
            return "dmy"
        if int(b) > 12:
            return "mdy"
    return "dmy"


def _parse_ts(d: str, t: str, order: str) -> float:
    a, b, y = d.split("/")
    day, month = (int(a), int(b)) if order == "dmy" else (int(b), int(a))
    year = int(y) + (2000 if len(y) == 2 else 0)
    t = t.upper().replace(".", "").strip()
    for fmt in ("%I:%M:%S %p", "%I:%M %p", "%I:%M:%S%p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
        try:
            tt = datetime.strptime(t, fmt)
            return datetime(year, month, day, tt.hour, tt.minute, tt.second).timestamp()
        except ValueError:
            continue
    return 0.0


def parse(path: Path, my_name: str, date_order: str = "auto") -> list[Message]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    order = detect_date_order(lines) if date_order == "auto" else date_order
    chat = f"txt:{path.stem}"
    me = my_name.strip().casefold()

    out: list[Message] = []
    for raw in lines:
        line = _norm(raw)
        m = _match(line)
        if not m:
            if out and line.strip() and out[-1].ts != -1:
                out[-1].text += "\n" + line          # continuation of a multi-line message
            continue
        sender, sep, text = m.group("rest").partition(": ")
        if not sep:
            # System notice ("Alex added you"): kept as a marker (ts=-1) so any
            # lines under it aren't glued onto the previous real message.
            out.append(Message(chat, False, "", -1))
            continue
        out.append(Message(chat, sender.strip().casefold() == me, text,
                           _parse_ts(m.group("d"), m.group("t"), order)))
    return [m for m in out if m.ts != -1]


def senders(path: Path) -> dict[str, int]:
    """Who speaks in this file, by message count -- used to catch a wrong MY_NAME."""
    counts: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = _match(_norm(raw))
        if m:
            sender, sep, _ = m.group("rest").partition(": ")
            if sep:
                counts[sender.strip()] = counts.get(sender.strip(), 0) + 1
    return counts
