"""Import a full WhatsApp backup: msgstore.db.crypt15 (+ its 64-hex key), or an
already-decrypted msgstore.db.

Decryption is delegated to `wadecrypt` (pip install wa-crypt-tools). The key is
the 64-character hex string WhatsApp shows when you turn on end-to-end encrypted
backups with the "use a 64-digit key" option.

Only 1-to-1 chats are read. Both address forms count: `s.whatsapp.net` (phone
number) and `lid` (WhatsApp's newer linked identity) -- skipping `lid` silently
drops a large and growing share of recent chats. Groups (`g.us`) are skipped:
a reply there answers several people at once and makes a misleading example.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from ..pairs import Message

TEXT_MESSAGE = 0     # message_type 0 = plain text


def decrypt(backup: Path, key: str, out_dir: Path) -> Path:
    exe = shutil.which("wadecrypt")
    if not exe:
        raise RuntimeError("`wadecrypt` not found. Install it with: pip install wa-crypt-tools")
    out = out_dir / "msgstore.db"
    # The key is passed as an argument, not written to disk here, so a plaintext
    # key file never lingers next to the backup.
    r = subprocess.run([exe, key.strip(), str(backup), str(out)], capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"wadecrypt failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return out


def read_db(db: Path) -> list[Message]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        chats = con.execute(
            """select c._id, j.raw_string
                 from chat c join jid j on c.jid_row_id = j._id
                where j.server in ('s.whatsapp.net', 'lid')"""
        ).fetchall()
        out: list[Message] = []
        for cid, raw_jid in chats:
            for from_me, text, ts in con.execute(
                """select from_me, text_data, timestamp from message
                    where chat_row_id = ? and message_type = ? and text_data is not null
                    order by timestamp, _id""",
                (cid, TEXT_MESSAGE),
            ):
                out.append(Message(f"db:{raw_jid}", bool(from_me), text, (ts or 0) / 1000))
        return out
    finally:
        con.close()


def load(backup: Path, key: str | None = None) -> list[Message]:
    if backup.suffix.startswith(".crypt"):
        if not key:
            raise ValueError("A .crypt15 backup needs --key (the 64-digit hex backup key).")
        with tempfile.TemporaryDirectory() as tmp:
            # Decrypted DB lives only in a temp dir and is gone when we return.
            return read_db(decrypt(backup, key, Path(tmp)))
    return read_db(backup)
