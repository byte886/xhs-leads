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


def test_dedupe_clients_by_uid_keeps_highest_intent():
    """一人多条评论必须收敛成一个人（界面「本篇线索」是人数，不是评论条数）。

    实测口径差：488 条评论声明 → 465 条评论记录 → 实际 376 人。
    """
    users = [
        {"uid": "u1", "intent": "low", "comment": "好看"},
        {"uid": "u1", "intent": "high", "comment": "多少钱", "matched_word": "多少钱"},
        {"uid": "u2", "intent": "middle", "comment": "在哪买"},
    ]
    out = server._dedupe_clients_by_uid(users)
    assert len(out) == 2
    by = {u["uid"]: u for u in out}
    assert by["u1"]["intent"] == "high"
    assert by["u1"]["matched_word"] == "多少钱"
    assert by["u2"]["intent"] == "middle"


def test_dedupe_clients_by_uid_empty():
    assert server._dedupe_clients_by_uid([]) == []


def test_collect_clients_excludes_author(tmp_path, monkeypatch):
    """作者本人评论不算线索（真机验证：488 条样本采到 465 条、作者 0 行）。

    入库前按 notes.user_id 过滤；作者回复/顶帖会虚增客户数。
    """
    import asyncio

    from src import browser_client  # noqa: E402

    _iso_db(tmp_path, monkeypatch)
    con = server._db()
    con.execute(
        "INSERT OR REPLACE INTO notes (feed_id, user_id, xsec_token) VALUES (?,?,?)",
        ("f1", "author-uid", "tok"),
    )
    con.commit()

    async def fake_clients(feed_id, token, keyword="", filters=None):
        return [
            {"uid": "author-uid", "nickname": "作者", "comment": "谢谢大家", "intent": "low"},
            {"uid": "u1", "nickname": "路人", "comment": "多少钱", "intent": "high"},
            {"uid": "u1", "nickname": "路人", "comment": "能便宜吗", "intent": "high"},
        ]

    async def ok_login():
        return None

    monkeypatch.setattr(browser_client, "clients", fake_clients)
    monkeypatch.setattr(server, "_require_login", ok_login)
    res = asyncio.run(server.collect_clients(server.ClientsReq(feed_id="f1")))
    assert res["total"] == 2          # 两条评论都留（同一人的不同内容）
    assert res["people"] == 1         # 人数按 uid 去重
    assert all(u["uid"] != "author-uid" for u in res["users"])


def _coverage_case(tmp_path, monkeypatch, declared, comments):
    """跑一次 /api/clients，返回 low_coverage 标记。comments 为抓到的人均评论数。"""
    import asyncio

    from src import browser_client

    _iso_db(tmp_path, monkeypatch)
    con = server._db()
    con.execute(
        "INSERT OR REPLACE INTO notes (feed_id, user_id, xsec_token, comment_count) VALUES (?,?,?,?)",
        ("f2", "author-uid", "tok", declared),
    )
    con.commit()

    async def fake_clients(feed_id, token, keyword="", filters=None):
        return [{"uid": f"u{i}", "nickname": "n", "comment": f"c{i}", "intent": "low"}
                for i in range(comments)]

    async def ok_login():
        return None

    monkeypatch.setattr(browser_client, "clients", fake_clients)
    monkeypatch.setattr(server, "_require_login", ok_login)
    return asyncio.run(server.collect_clients(server.ClientsReq(feed_id="f2")))


def test_low_coverage_flags_interrupted_collect(tmp_path, monkeypatch):
    """声明 343 条只采到 85 条（实测被抢滚轮的情形）必须打标，界面才能提示重试。"""
    res = _coverage_case(tmp_path, monkeypatch, 343, 85)
    assert res["declared"] == 343
    assert res["low_coverage"] is True


def test_low_coverage_ok_when_near_declared(tmp_path, monkeypatch):
    """正常覆盖 95% 左右（实测 95.3%/95.9%）不打标，避免每次采集都报错。"""
    res = _coverage_case(tmp_path, monkeypatch, 343, 329)
    assert res["low_coverage"] is False


def test_low_coverage_ignores_small_notes_and_missing_declared(tmp_path, monkeypatch):
    """声明数缺失或笔记很小（<30）时不判断，避免误报。"""
    assert _coverage_case(tmp_path, monkeypatch, 0, 10)["low_coverage"] is False
    assert _coverage_case(tmp_path, monkeypatch, 20, 8)["low_coverage"] is False
