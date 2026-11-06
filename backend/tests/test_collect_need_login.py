"""采集撞到登录墙的契约（R-6.53z8p）：

用户口径——采集过程中一旦需要登录，本次采集就此结束（已采保留），
并立刻推事件让左窗知道，不再挂起等待、不再续采。
"""
import asyncio
import sqlite3
from types import SimpleNamespace

from src import server
from src.browser_client import BrowserLoginRequiredError


def _req(limit: int = 3):
    return SimpleNamespace(
        keyword="翡翠", limit=limit, interval=0, fast=False,
        sort="", note_type="", time_range="", scope="", distance="",
    )


def _ctx(con):
    return {"req": _req(), "con": con, "cache": {}, "account_id": "default", "now": ""}


def _state(saved: int = 0):
    return {
        "status": "running", "phase": "detail", "collected": saved, "saved": saved,
        "partial_items": [], "keyword": "翡翠", "limit": 3,
    }


# --------------------------------------------------------- 断点事实源（保留）

def test_seen_ids_reads_notes_table():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE notes (feed_id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO notes VALUES ('a')")
    assert server._collect_seen_ids(con) == {"a"}


def test_seen_ids_returns_empty_on_broken_table():
    """表缺失/查询失败不得中断采集，按空集处理。"""
    assert server._collect_seen_ids(sqlite3.connect(":memory:")) == set()


# --------------------------------------------------------- 撞墙即结束

def test_need_login_ends_task_and_keeps_saved():
    """撞墙 → 任务结束（ended_need_login），已采条数与原因都写进状态。"""
    state = _state(saved=2)
    server._finalize_need_login(state, "default", RuntimeError("小红书要求登录"))

    assert state["status"] == "ended_need_login"
    assert state["phase"] == "done"
    assert "需要登录" in state["note"]
    assert "2 条保留" in state["note"]
    assert state["saved"] == 2


def test_need_login_without_data_says_so():
    """一条都没采到时，note 也要说清楚（而不是只说「需要登录」让人以为采到了）。"""
    state = _state(saved=0)
    server._finalize_need_login(state, "default", RuntimeError("x"))
    assert "未采到数据" in state["note"]


def test_snapshot_reports_need_login_for_left_window():
    """左窗靠快照/事件判断，必须有一个明确的 need_login 标记。"""
    state = _state()
    server._finalize_need_login(state, "default", RuntimeError("x"))
    assert server._state_snapshot(state)["need_login"] is True
    assert server._state_snapshot(_state())["need_login"] is False


def test_need_login_pushes_event_to_subscribers():
    """不推事件，左窗就只能一直停在「正在采集」——这是本契约的核心要求。"""
    state = _state(saved=1)
    q = asyncio.Queue()
    server._EVENT_SUBS.append(q)
    try:
        server._finalize_need_login(state, "default", RuntimeError("x"))
    finally:
        server._EVENT_SUBS.remove(q)
    ev = q.get_nowait()
    assert ev["kind"] == "collect"
    assert ev["data"]["need_login"] is True
    assert ev["data"]["status"] == "ended_need_login"


# --------------------------------------------------------- 撞墙不再重试

def test_run_rounds_does_not_retry_after_login_wall(monkeypatch):
    """撞墙只收尾一次，不做「等登录再续采」的循环重试。"""
    calls = {"n": 0}

    async def fake_round(state, req, ctx, account_id):
        calls["n"] += 1
        raise BrowserLoginRequiredError("小红书要求登录")

    monkeypatch.setattr(server, "_collect_round", fake_round)
    state = _state()

    res = asyncio.run(server._collect_run_rounds(state, _req(), _ctx(None), "default"))

    assert res is None
    assert calls["n"] == 1
    assert state["status"] == "ended_need_login"
