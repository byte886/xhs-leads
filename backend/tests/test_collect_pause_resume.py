"""采集撞墙暂停 / 扫码续采（R-6.53z8c）单测。

只描述现行契约：撞墙不退出任务、挂起等事件、恢复后跳过已入库续采。
"""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from src import server
from src.browser_client import BrowserLoginRequiredError


def _req(limit: int = 3):
    return SimpleNamespace(
        keyword="翡翠", limit=limit, interval=0, fast=False,
        sort="", note_type="", time_range="", scope="", distance="",
    )


def _con_with(ids):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE notes (feed_id TEXT PRIMARY KEY)")
    for i in ids:
        con.execute("INSERT INTO notes VALUES (?)", (i,))
    return con


def _ctx(con):
    return {"req": _req(), "con": con, "cache": {}, "account_id": "default", "now": ""}


def _state():
    return {
        "status": "running", "stop_event": False, "collected": 0, "saved": 0,
        "partial_items": [], "wall_count": 0, "giveup": False,
    }


# --------------------------------------------------------- 断点事实源

def test_seen_ids_reads_notes_table():
    """断点不另存游标：直接查 notes 表的 feed_id。"""
    con = _con_with(["a", "b"])
    assert server._collect_seen_ids(con) == {"a", "b"}


def test_seen_ids_returns_empty_on_broken_table():
    """表缺失/查询失败不得中断采集，按空集处理。"""
    con = sqlite3.connect(":memory:")
    assert server._collect_seen_ids(con) == set()


# --------------------------------------------------------- 暂停与超时

def test_pause_returns_true_when_login_notified(monkeypatch):
    monkeypatch.setattr(server, "COLLECT_PAUSE_MAX", 3.0)
    state = _state()

    async def run():
        async def notify():
            await asyncio.sleep(0.05)
            state["resume_event"].set()

        t = asyncio.create_task(notify())
        ok = await server._pause_for_login(state, RuntimeError("需要登录"))
        await t
        return ok

    assert asyncio.run(run()) is True
    assert state["status"] == "running" or state["status"] == "paused_need_login"


def test_pause_times_out_and_keeps_data(monkeypatch):
    """用户不在场：超时返回 False（外层转「部分完成」，已采数据保留）。"""
    monkeypatch.setattr(server, "COLLECT_PAUSE_MAX", 0.05)
    state = _state()
    state["saved"] = 2
    assert asyncio.run(server._pause_for_login(state, RuntimeError("x"))) is False
    assert state["note"].startswith("等待登录超时")
    assert state["saved"] == 2


def test_pause_returns_false_on_giveup(monkeypatch):
    """用户选「就用这些」：置 giveup 并唤醒 → 不续采。"""
    monkeypatch.setattr(server, "COLLECT_PAUSE_MAX", 3.0)
    state = _state()

    async def run():
        async def give_up():
            await asyncio.sleep(0.05)
            state["giveup"] = True
            state["resume_event"].set()

        t = asyncio.create_task(give_up())
        ok = await server._pause_for_login(state, RuntimeError("x"))
        await t
        return ok

    assert asyncio.run(run()) is False


# --------------------------------------------------------- 续采跳过已入库

def test_wall_pauses_then_resume_skips_ingested(monkeypatch):
    """撞墙 → 暂停 → 扫码恢复 → 第二轮只补没入库的那些。"""
    monkeypatch.setattr(server, "COLLECT_PAUSE_MAX", 3.0)
    calls = {"n": 0, "one": []}

    async def fake_search(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserLoginRequiredError("小红书要求登录")
        return {"items": [{"feed_id": "A"}, {"feed_id": "B"}]}

    async def fake_one(item, state, ctx):
        calls["one"].append(item["feed_id"])

    monkeypatch.setattr(server, "search_via_browser", fake_search)
    monkeypatch.setattr(server, "_collect_one", fake_one)

    state = _state()
    con = _con_with(["A"])          # A 是中断前已入库的
    ctx = _ctx(con)

    async def run():
        async def notify():
            await asyncio.sleep(0.05)
            state["resume_event"].set()

        t = asyncio.create_task(notify())
        res = await server._collect_run_rounds(state, _req(), ctx, "default")
        await t
        return res

    res = asyncio.run(run())
    assert res is not None
    assert calls["n"] == 2                 # 恢复后确实重新搜了一轮
    assert calls["one"] == ["B"]           # 已入库的 A 被跳过
    assert state["resumed"] is True


def test_wall_limit_stops_after_three(monkeypatch):
    """连续撞墙达上限即判账号受限，不再无限重试（风控累积效应）。"""
    async def always_wall(*a, **k):
        raise BrowserLoginRequiredError("小红书要求登录")

    monkeypatch.setattr(server, "search_via_browser", always_wall)
    monkeypatch.setattr(server, "COLLECT_PAUSE_MAX", 3.0)
    state = _state()
    state["saved"] = 1
    con = _con_with(["A"])

    async def run():
        async def notify_each():
            for _ in range(3):
                await asyncio.sleep(0.04)
                state["resume_event"].set()

        t = asyncio.create_task(notify_each())
        res = await server._collect_run_rounds(state, _req(), _ctx(con), "default")
        await t
        return res

    res = asyncio.run(run())
    assert res is None
    assert state["status"] == "stopped"
    assert "账号受限" in state["note"]


# --------------------------------------------------------- 补全阶段撞墙必须冒泡

def test_collect_one_propagates_login_wall(monkeypatch):
    """单条补全撞墙不得被 except Exception 吞掉（否则会拿残缺数据入库）。"""
    async def fake_detail(item, account):
        raise BrowserLoginRequiredError("小红书要求登录")

    monkeypatch.setattr(server, "get_feed_detail", fake_detail)
    state = _state()
    ctx = _ctx(_con_with([]))

    with pytest.raises(BrowserLoginRequiredError):
        asyncio.run(server._collect_one({"feed_id": "X"}, state, ctx))


# --------------------------------------------------------- 登录成功唤醒

def test_notify_login_ok_wakes_only_paused(monkeypatch):
    monkeypatch.setattr(server, "_COLLECT_TASKS", {
        "t1": {"status": "paused_need_login", "resume_event": asyncio.Event()},
        "t2": {"status": "running", "resume_event": asyncio.Event()},
        "t3": {"status": "paused_need_login"},
    })
    assert server._notify_login_ok() == 1
    assert server._COLLECT_TASKS["t1"]["resume_event"].is_set()
    assert not server._COLLECT_TASKS["t2"]["resume_event"].is_set()


# --------------------------------------------------------- 端点契约

def test_collect_resume_and_giveup_contract(monkeypatch):
    from fastapi.testclient import TestClient
    from src.server import app

    monkeypatch.setattr(server, "_startup_cleanup", lambda: None)
    ev = asyncio.Event()
    monkeypatch.setattr(server, "_COLLECT_TASKS", {
        "t1": {"status": "paused_need_login", "resume_event": ev, "saved": 2},
        "t2": {"status": "running"},
    })
    with TestClient(app) as c:
        r = c.post("/api/collect_resume", json={"task_id": "t1"})
        assert r.json()["ok"] is True
        r = c.post("/api/collect_resume", json={"task_id": "t2"})
        assert r.json()["ok"] is False
        r = c.post("/api/collect_giveup", json={"task_id": "t1"})
        assert r.json()["ok"] is True
        assert server._COLLECT_TASKS["t1"]["giveup"] is True
        r = c.post("/api/collect_giveup", json={"task_id": "none"})
        assert r.json()["ok"] is False
