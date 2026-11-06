"""全量同步配对：标题优先、日期兜底、认不准就不硬凑；以及增量计算。"""
import sqlite3

from src.server import (_apply_creator_card, _backfill_note_id, _delta_text,
                        _match_by_note, _match_by_title, _match_log,
                        _ensure_stats_history)


def _logs(*rows):
    out = []
    for r in rows:
        out.append({"id": r[0], "title": r[1], "created_at": r[2],
                    "note_id": r[3] if len(r) > 3 else None})
    return out


def test_match_by_title_exact():
    logs = _logs((1, "AI游戏", "2025-02-03 14:42"), (2, "转租福田区宝鸿苑合租单间 1400", "2026-07-02 12:32"))
    assert _match_by_title(logs, "AI游戏") == 1


def test_match_by_title_empty_returns_none():
    logs = _logs((1, "AI游戏", "2025-02-03 14:42"))
    assert _match_by_title(logs, "") is None
    assert _match_by_title(logs, "   ") is None


def test_match_falls_back_to_day_when_title_empty():
    """图文可能没标题：这时按发布日期认。"""
    logs = _logs((1, "随便", "2026-07-02 12:32"))
    assert _match_log(logs, "", "2026-07-02 12:32") == 1


def test_match_does_not_guess_when_same_day_multiple():
    """同一天发了多条、又没有标题，不能随便挑一条。"""
    logs = _logs((1, "A", "2026-07-02 09:00"), (2, "B", "2026-07-02 18:00"))
    assert _match_log(logs, "", "2026-07-02 12:32") is None


def test_delta_text():
    assert _delta_text("6", "4") == "+2"
    assert _delta_text("1", "3") == "-2"
    assert _delta_text("6", "6") == ""
    assert _delta_text("", "4") == ""
    assert _delta_text("6", None) == ""


def _mem_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE publish_logs (id INTEGER PRIMARY KEY, title TEXT, stats_json TEXT)")
    con.execute("INSERT INTO publish_logs (id,title) VALUES (1,'AI游戏')")
    _ensure_stats_history(con)
    return con


def test_apply_card_stores_stats_and_marks_delta():
    con = _mem_con()
    card = {"title": "AI游戏", "time": "2025-02-03 14:42", "stats": ["93", "1", "6", "1", "0"]}
    first = _apply_creator_card(con, 1, card)
    assert first["views"] == "93" and first["liked_count"] == "6"
    assert first["deltas"]["views"] == ""          # 第一次没有可比的上一次

    card2 = {"title": "AI游戏", "time": "2025-02-03 14:42", "stats": ["105", "1", "8", "1", "0"]}
    second = _apply_creator_card(con, 1, card2)
    assert second["deltas"]["views"] == "+12"
    assert second["deltas"]["liked_count"] == "+2"
    assert second["deltas"]["collected_count"] == ""
    assert con.execute("SELECT COUNT(*) FROM publish_stats_history").fetchone()[0] == 2


# ---- A：笔记 id 精确匹配（不依赖标题是否存在） ----
def test_match_by_note_exact():
    logs = _logs((1, "", "2026-07-02 12:32", "6a45e9e0000000001702e578"))
    assert _match_by_note(logs, "6a45e9e0000000001702e578") == 1


def test_match_by_note_beats_title_conflict():
    """id 最权威：标题对不上也照样认出来（用户改了标题 / 卡片标题被截断都能救）。"""
    logs = _logs((1, "旧标题", "2025-02-03 14:42", "67a0653d0000000018012c40"))
    assert _match_log(logs, "完全不同的标题", "2025-02-03 14:42", "67a0653d0000000018012c40") == 1


def test_match_by_note_empty_falls_back_to_title():
    logs = _logs((1, "AI游戏", "2025-02-03 14:42", None))
    assert _match_by_note(logs, "") is None
    assert _match_log(logs, "AI游戏", "2025-02-03 14:42", "") == 1


def test_backfill_note_id_stores_first_time():
    """第一次对上就把 id 记住，之后永久精确匹配。"""
    con = sqlite3.connect(":memory:")
    _ensure_stats_history(con)
    con.execute("CREATE TABLE publish_logs (id INTEGER PRIMARY KEY, note_id TEXT)")
    con.execute("INSERT INTO publish_logs (id, note_id) VALUES (1, NULL)")
    _backfill_note_id(con, 1, {"note_id": "67a0653d0000000018012c40"})
    assert con.execute("SELECT note_id FROM publish_logs WHERE id=1").fetchone()[0] == "67a0653d0000000018012c40"
