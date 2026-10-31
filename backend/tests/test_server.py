"""server 纯逻辑单测：图片名/文本解析/登录态判定/落库/图片清理/发布护栏链。"""
import datetime
import json

import pytest

from src import server
from src.server import (
    _cleanup_orphan_images, _cookie_items, _duplicate_published, _img_name,
    _last_publish_ago_minutes, _parse_cookie_text, _publish_count_today,
    _publish_guard, _publish_guard_quota, _publish_guard_time, _publish_week_days,
    _rank_and_insert, _read_cookie_status, _risk_blocked_until,
)

NOON = datetime.datetime(2026, 1, 1, 12, 0)


class TestImgName:
    def test_hash_replaced(self):
        assert _img_name("abc#def") == "abc_def_cover.jpg"

    def test_suffix(self):
        assert _img_name("id123", suffix="p1") == "id123_p1.jpg"


class TestParseCookieText:
    def test_multi_line_and_semicolon(self):
        c = _parse_cookie_text("a=1; b=2\nc=3")
        assert {x["name"]: x["value"] for x in c} == {"a": "1", "b": "2", "c": "3"}
        assert all(x["domain"] == ".xiaohongshu.com" for x in c)

    def test_skip_invalid(self):
        c = _parse_cookie_text("a=1;;   ; =x; d=")
        names = {x["name"] for x in c}
        assert names == {"a", "d"}  # 空段/无名段跳过；d=（空 value）保留

    def test_no_equals_segment_skipped(self):
        # 非空但无 "=" 的段（如 "abc"）必须跳过不崩（or->and 于 624 行应被杀）
        assert _parse_cookie_text("abc") == []

    def test_http_only_set(self):
        c = {x["name"]: x for x in _parse_cookie_text("web_session=1; a1=2; other=3")}
        assert c["web_session"]["httpOnly"] is True
        assert c["other"]["httpOnly"] is False


class TestReadCookieStatus:
    def test_logged_in(self, tmp_path):
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "abc", "expires": None}]}))
        ok, ws, exp = _read_cookie_status(p)
        assert ok and ws == "abc" and exp

    def test_expired(self, tmp_path):
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "abc", "expires": 1}]}))
        ok, ws, exp = _read_cookie_status(p)
        assert not ok and exp is False

    def test_missing_file(self, tmp_path):
        ok, ws, exp = _read_cookie_status(tmp_path / "nope.json")
        assert not ok and not ws and exp


class TestRankAndInsert:
    def test_inserts_scored(self, tmp_db):
        n = {"feed_id": "f1", "liked_count": 1000, "fans_count": 100, "publish_time": None}
        assert _rank_and_insert(tmp_db, n, "翡翠", "2026-01-01T00:00:00") is True
        row = tmp_db.execute("SELECT * FROM notes WHERE feed_id='f1'").fetchone()
        assert row["keyword"] == "翡翠"
        assert row["score"] > 0

    def test_bad_note_no_crash(self, tmp_db):
        # 空 dict：评分不抛、插入容忍（TEXT 主键允许 NULL）；函数不抛异常即可
        _rank_and_insert(tmp_db, {}, "k", "t")


class TestCleanupOrphanImages:
    def test_keeps_referenced_removes_rest(self, tmp_images, monkeypatch, tmp_path):
        (tmp_images / "ref_cover.jpg").write_bytes(b"x")
        (tmp_images / "orphan_cover.jpg").write_bytes(b"x")
        (tmp_images / "ref_p1.jpg").write_bytes(b"x")

        class FakeRows:
            def fetchall(self):
                return [("ref",)]

        class FakeCon:
            def execute(self, *a):
                return FakeRows()
            def close(self):
                pass
        monkeypatch.setattr(server, "_db", lambda: FakeCon())
        assert _cleanup_orphan_images() == 1
        assert (tmp_images / "ref_cover.jpg").exists()
        assert (tmp_images / "ref_p1.jpg").exists()
        assert not (tmp_images / "orphan_cover.jpg").exists()


class TestCookieItems:
    def test_sorted_and_ws(self):
        cookies = [
            {"name": "zz", "value": "1"},
            {"name": "web_session", "value": "0123456789abcdef"},
        ]
        items, ws = _cookie_items(cookies)
        assert ws == "0123456789abcdef"
        assert items[0]["name"] == "web_session"  # 排序后 web_session 在前


def _now_fixed(monkeypatch, dt=NOON):
    monkeypatch.setattr(server, "_now", lambda: dt)


def _log(con, *, status="success", raw="", title="t", content="c", created="2026-01-01T00:00:00"):
    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at, raw) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("f", title, content, "", "仅自己可见", status, created, raw))


class TestPublishHelpers:
    def test_risk_blocked_none(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="failed", raw="普通失败", created="2026-01-01T11:00:00")
        assert _risk_blocked_until(tmp_db) is None

    def test_risk_blocked_hit(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="failed", raw="发布失败：频繁", created="2026-01-01T11:00:00")
        until = _risk_blocked_until(tmp_db)
        assert until and until.startswith("2026-01-02")

    def test_week_days_count(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", created="2026-01-01T09:00:00")
        _log(tmp_db, status="success", created="2026-01-02T09:00:00")
        assert _publish_week_days(tmp_db) == 2

    def test_last_publish_ago(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", created="2026-01-01T11:00:00")
        assert _last_publish_ago_minutes(tmp_db) == pytest.approx(60.0)

    def test_count_today(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", created="2026-01-01T08:00:00")
        assert _publish_count_today(tmp_db) == 1

    def test_duplicate(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", title="同一标题", content="同一正文", created="2026-01-01T10:00:00")
        assert _duplicate_published(tmp_db, "同一标题", "同一正文") is True
        assert _duplicate_published(tmp_db, "别的标题", "别的正文") is False


class TestPublishGuard:
    def test_time_risk_blocked(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="failed", raw="频繁", created="2026-01-01T11:00:00")
        r = _publish_guard_time(tmp_db)
        assert r and r["cooldown"] and "风控熔断" in r["raw"]

    def test_time_outside_hours(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch, datetime.datetime(2026, 1, 1, 23, 0))
        assert _publish_guard_time(tmp_db) is not None

    def test_time_cooldown(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", created="2026-01-01T11:50:00")
        assert _publish_guard_time(tmp_db) is not None

    def test_time_ok(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", created="2026-01-01T01:00:00")
        assert _publish_guard_time(tmp_db) is None

    def test_quota_daily(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        for i in range(server.PUBLISH_DAILY_MAX):
            _log(tmp_db, status="success", created=f"2026-01-01T0{i}:00:00")
        req = type("R", (), {"title": "x", "content": "y", "desc": ""})()
        assert _publish_guard_quota(tmp_db, req) is not None

    def test_quota_week(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        for i in range(server.WEEKLY_MAX_DAYS):
            _log(tmp_db, status="success", created=f"2025-12-{26+i:02d}T09:00:00")
        req = type("R", (), {"title": "x", "content": "y", "desc": ""})()
        assert _publish_guard_quota(tmp_db, req) is not None

    def test_quota_duplicate(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        _log(tmp_db, status="success", title="重", content="复", created="2026-01-01T10:00:00")
        req = type("R", (), {"title": "重", "content": "复", "desc": ""})()
        assert _publish_guard_quota(tmp_db, req) is not None

    def test_full_guard_passes(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        req = type("R", (), {"title": "新", "content": "文", "desc": ""})()
        assert _publish_guard(tmp_db, req) is None


class TestPublishGuardBoundaries:
    def test_time_ok_start_boundary(self, tmp_db, monkeypatch):
        # START 边界：10:00 允许发布（<=->< 应被杀）
        _now_fixed(monkeypatch, datetime.datetime(2026, 1, 1, 10, 0))
        assert _publish_guard_time(tmp_db) is None

    def test_time_ok_2130(self, tmp_db, monkeypatch):
        # END 边界 21:30 允许（<=-><）且分钟换算正确（/->* 应被杀）
        _now_fixed(monkeypatch, datetime.datetime(2026, 1, 1, 21, 30))
        assert _publish_guard_time(tmp_db) is None

    def test_duplicate_empty_db(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        assert _duplicate_published(tmp_db, "x", "y") is False

    def test_week_days_empty(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        assert _publish_week_days(tmp_db) == 0

    def test_count_today_empty(self, tmp_db, monkeypatch):
        _now_fixed(monkeypatch)
        assert _publish_count_today(tmp_db) == 0

    def test_cooldown_exact_boundary(self, tmp_db, monkeypatch):
        # ago == limit(180)：不拒绝（ago<limit 边界，<-><= 应被杀）
        _now_fixed(monkeypatch)
        monkeypatch.setattr(server.random, "uniform", lambda a, b: 0.0)
        _log(tmp_db, status="success", created="2026-01-01T09:00:00")
        assert _publish_guard_time(tmp_db) is None
