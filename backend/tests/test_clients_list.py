"""O1-3 客户库接口测试：意向分级落库 / 档位排序 / intent 过滤 / 新增字段。

注意：clients_list 内部经 _db() 读模块级 DB_PATH，测试必须 monkeypatch server.DB_PATH
指向临时文件，否则会读/写真实 data/xhs_notes.db（无效且污染）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))

from src import server  # noqa: E402


def _iso_db(tmp_path, monkeypatch):
    """把 server.DB_PATH 指向临时库并返回建好表的连接。"""
    p = tmp_path / "test.db"
    monkeypatch.setattr(server, "DB_PATH", p)
    con = server._db()  # 建表在临时库
    return con


def _seed(con, rows):
    for r in rows:
        con.execute(
            "INSERT OR IGNORE INTO clients "
            "(uid, feed_id, nickname, comment, profile, created_at, intent, matched_word, ip_location, comment_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                r["uid"], r.get("feed_id", "f1"), r["nickname"], r["comment"], r.get("profile", ""),
                r.get("created_at", "2026-10-28T10:00:00"), r["intent"], r.get("matched_word", ""),
                r.get("ip_location", ""), r.get("comment_time", ""),
            ),
        )
    con.commit()
    con.close()


def test_clients_list_high_first(tmp_path, monkeypatch):
    """档位排序：高意向置顶，其次按评论次数。"""
    con = _iso_db(tmp_path, monkeypatch)
    _seed(con, [
        {"uid": "a1", "nickname": "低意", "comment": "好看", "intent": "low"},
        {"uid": "a2", "nickname": "高意", "comment": "多少钱", "intent": "high", "matched_word": "多少钱"},
        {"uid": "a3", "nickname": "中意", "comment": "想要", "intent": "middle"},
        {"uid": "a4", "nickname": "上意", "comment": "求教程", "intent": "upper"},
    ])
    rows = _call_clients_list()
    assert rows[0]["uid"] == "a2"  # 高意向置顶
    assert rows[1]["uid"] == "a4"  # 上意向
    assert rows[2]["uid"] == "a3"  # 中意向
    assert rows[3]["uid"] == "a1"  # 低意向


def test_clients_list_intent_filter(tmp_path, monkeypatch):
    con = _iso_db(tmp_path, monkeypatch)
    _seed(con, [
        {"uid": "a1", "nickname": "高1", "comment": "多少钱", "intent": "high"},
        {"uid": "a2", "nickname": "低1", "comment": "好看", "intent": "low"},
    ])
    rows = _call_clients_list(intent="high")
    assert len(rows) == 1 and rows[0]["uid"] == "a1"


def test_clients_list_fields(tmp_path, monkeypatch):
    """新字段透出：matched_word / ip_location / comment_time。"""
    con = _iso_db(tmp_path, monkeypatch)
    _seed(con, [
        {"uid": "a1", "nickname": "高1", "comment": "加微信", "intent": "high",
         "matched_word": "微信", "ip_location": "广东", "comment_time": "3天前"},
    ])
    rows = _call_clients_list()
    assert rows[0]["matched_word"] == "微信"
    assert rows[0]["ip_location"] == "广东"
    assert rows[0]["comment_time"] == "3天前"


def test_max_intent_picks_highest(tmp_path, monkeypatch):
    """同一用户多条评论（跨笔记）取最高档（低+高 → 高）。"""
    con = _iso_db(tmp_path, monkeypatch)
    _seed(con, [
        {"uid": "a1", "feed_id": "f1", "nickname": "同人", "comment": "好看", "intent": "low"},
        {"uid": "a1", "feed_id": "f2", "nickname": "同人", "comment": "多少钱", "intent": "high", "matched_word": "多少钱"},
    ])
    rows = _call_clients_list()
    assert len(rows) == 1  # 按 uid 聚合
    assert rows[0]["intent"] == "high"
    assert rows[0]["matched_word"] == "多少钱"


def _call_clients_list(feed_id: str = "", intent: str = ""):
    """直接调用路由函数（避免起真实 HTTP 服务）。"""
    return server.clients_list(feed_id=feed_id, intent=intent)["clients"]
