import sqlite3
from datetime import datetime

from conftest import EXAMPLES
from copilot.importers import msgstore, txt_export


def test_android_export(tmp_path):
    msgs = txt_export.parse(EXAMPLES / "WhatsApp Chat with Alex.txt", "Sam")
    assert msgs[0].text == "morning! you free for lunch tomorrow?" and not msgs[0].me
    assert msgs[1].me
    # The encryption notice has no "Name: " and must not become a message.
    assert not any("end-to-end" in m.text for m in msgs)
    assert datetime.fromtimestamp(msgs[0].ts) == datetime(2026, 8, 3, 9, 12)


def test_ios_export():
    msgs = txt_export.parse(EXAMPLES / "WhatsApp Chat with Jordan.txt", "sam")   # case-insensitive
    assert msgs[1].me and msgs[1].text == "yep still on"
    assert datetime.fromtimestamp(msgs[0].ts) == datetime(2026, 8, 2, 10, 4, 11)


def test_multiline_and_ampm_and_month_first(tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text(
        "12/31/23, 9:15 PM - Kim: line one\nline two\n"
        "12/31/23, 9:16 PM - Kim added Lee\n"
        "stray line under a notice\n"
        "12/31/23, 9:20 PM - Me Me: ok\n",
        encoding="utf-8",
    )
    assert txt_export.detect_date_order(f.read_text(encoding="utf-8").splitlines()) == "mdy"
    msgs = txt_export.parse(f, "Me Me")
    assert [m.text for m in msgs] == ["line one\nline two", "ok"]
    assert datetime.fromtimestamp(msgs[0].ts) == datetime(2023, 12, 31, 21, 15)


def test_msgstore_reads_phone_and_lid_but_not_groups(tmp_path):
    db = tmp_path / "msgstore.db"
    con = sqlite3.connect(db)
    con.executescript("""
        create table jid (_id integer primary key, raw_string text, server text);
        create table chat (_id integer primary key, jid_row_id integer);
        create table message (_id integer primary key, chat_row_id integer, from_me integer,
                              text_data text, timestamp integer, message_type integer);
        insert into jid values (1,'111@s.whatsapp.net','s.whatsapp.net'),
                               (2,'222@lid','lid'), (3,'333@g.us','g.us');
        insert into chat values (10,1),(20,2),(30,3);
        insert into message values
          (1,10,0,'hi phone',1000,0),(2,10,1,'hey',2000,0),
          (3,20,0,'hi lid',1000,0),(4,20,1,'yo',2000,0),
          (5,30,0,'group msg',1000,0),
          (6,10,0,null,3000,1);
    """)
    con.commit()
    con.close()
    msgs = msgstore.load(db)
    assert sorted(m.text for m in msgs) == ["hey", "hi lid", "hi phone", "yo"]
    assert {m.chat for m in msgs} == {"db:111@s.whatsapp.net", "db:222@lid"}
