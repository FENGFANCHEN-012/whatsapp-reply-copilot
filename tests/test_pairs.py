from conftest import EXAMPLES
from copilot.importers import txt_export
from copilot.pairs import Message, build_pairs

H = 3600


def M(me, text, ts):
    return Message("c", me, text, ts)


def test_bursts_merge_on_both_sides():
    pairs = build_pairs([M(False, "free tmr?", 0), M(False, "for lunch", 10),
                         M(True, "yeah", 20), M(True, "where?", 30)])
    assert len(pairs) == 1
    assert pairs[0]["in"] == "free tmr?\nfor lunch" and pairs[0]["out"] == "yeah\nwhere?"


def test_context_is_what_came_before_the_burst():
    pairs = build_pairs([M(False, "a", 0), M(True, "b", 10), M(False, "c", 20), M(True, "d", 30)])
    second = [p for p in pairs if p["in"] == "c"][0]
    assert second["context"] == [{"dir": "in", "text": "a"}, {"dir": "out", "text": "b"}]


def test_reply_hours_later_still_counts_but_not_days_later():
    T = 1_000_000
    ok = build_pairs([M(False, "hi", T), M(True, "sorry just saw", T + 8 * H)])
    assert [p["out"] for p in ok] == ["sorry just saw"]
    assert build_pairs([M(False, "hi", T), M(True, "sorry just saw", T + 30 * H)]) == []


def test_long_silence_resets_context():
    pairs = build_pairs([M(False, "a", 0), M(True, "b", 10),
                         M(False, "new day", 10 * H), M(True, "hey", 10 * H + 5)])
    assert [p for p in pairs if p["in"] == "new day"][0]["context"] == []


def test_placeholders_and_my_openers_are_skipped():
    pairs = build_pairs([M(True, "i start", 0), M(False, "<Media omitted>", 5),
                         M(False, "look", 6), M(True, "nice", 7)])
    assert len(pairs) == 1 and pairs[0]["in"] == "look" and pairs[0]["out"] == "nice"


def test_chat_ids_are_hashed():
    pairs = build_pairs(txt_export.parse(EXAMPLES / "WhatsApp Chat with Alex.txt", "Sam"))
    assert len(pairs) >= 10
    assert all("Alex" not in p["chat"] for p in pairs)
