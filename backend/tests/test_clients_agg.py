"""O1-4 客户库聚合接口测试：总数/意向分布/属地TOP/近7天/留联系方式计数。"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))

from src import server  # noqa: E402


def _iso_db(tmp_path, monkeypatch):
    p = tmp_path / "test.db"
    monkeypatch.setattr(server, "DB_PATH", p)
    return server._db()


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


def test_agg_counts(tmp_path, monkeypatch):
    today = datetime.date.today()
    old = (today - datetime.timedelta(days=30)).isoformat()
    _seed(_iso_db(tmp_path, monkeypatch), [
        {"uid": "a1", "nickname": "高1", "comment": "加微信", "intent": "high", "matched_word": "微信",
         "ip_location": "广东", "created_at": f"{today}T10:00:00"},
        {"uid": "a2", "nickname": "上1", "comment": "求教程", "intent": "upper",
         "ip_location": "四川", "created_at": f"{today}T09:00:00"},
        {"uid": "a3", "nickname": "中1", "comment": "想要", "intent": "middle",
         "created_at": f"{today.isoformat()}", "ip_location": "广东"},
        {"uid": "a4", "nickname": "低1", "comment": "好看", "intent": "low", "ip_location": "湖北",
         "created_at": f"{old}T10:00:00"},
    ])
    agg = server.clients_agg()
    assert agg["total"] == 4
    # 意向分布按 高→上→中→低 顺序
    assert [d["intent"] for d in agg["intent_dist"]] == ["high", "upper", "middle", "low"]
    assert [d["count"] for d in agg["intent_dist"]] == [1, 1, 1, 1]
    # 属地 TOP：广东 2 > 四川 1 > 湖北 1
    assert agg["ip_top"][0]["ip"] == "广东" and agg["ip_top"][0]["count"] == 2
    # 近 7 天：今天有 3 条（a1/a2/a3 有 created_at 且是今天；a4 默认 2026-10-28 可能不在近7天）
    today_str = today.isoformat()
    week = {d["date"]: d["count"] for d in agg["week"]}
    assert week[today_str] == 3
    assert len(agg["week"]) == 7
    # 留联系方式：a1 命中"微信"
    assert agg["contact"] == 1


def test_agg_empty(tmp_path, monkeypatch):
    _iso_db(tmp_path, monkeypatch)
    agg = server.clients_agg()
    assert agg["total"] == 0
    assert [d["count"] for d in agg["intent_dist"]] == [0, 0, 0, 0]
    assert agg["ip_top"] == []
    assert len(agg["week"]) == 7 and all(d["count"] == 0 for d in agg["week"])
    assert agg["contact"] == 0
