"""引擎窗口开关（set_window）的守卫行为。

原「事件转发」相关测试随能力下线一并删除（R-6.53z8p）。
"""
import asyncio

from src import config, engine_pool, engine_window, server


def test_set_window_refuses_while_collecting():
    """采集进行中切换窗口会重启引擎，必须先拒绝。"""
    server._COLLECT_TASKS["t1"] = {"status": "running", "account_id": "default"}
    try:
        r = asyncio.run(engine_window.set_window("default", on=True))
    finally:
        server._COLLECT_TASKS.pop("t1", None)
    assert r["ok"] is False
    assert "采集任务进行中" in r["error"]


def test_set_window_noop_when_already_target_mode(monkeypatch):
    """已经是目标模式时不重启引擎（重启代价高且会断登录态）。"""
    monkeypatch.setattr(config, "get_config", lambda: {"headless": False})
    r = asyncio.run(engine_window.set_window("default", on=True))
    assert r["ok"] is True
    assert r["note"] == "已是目标模式，无需切换"


def test_set_window_noop_for_headless_target(monkeypatch):
    """反向：要回无头但本来就是无头（default 已禁止切无头，此处用普通账号）。"""
    monkeypatch.setattr(config, "account_headless", lambda _aid: True)
    r = asyncio.run(engine_window.set_window("acc_x", on=False))
    assert r["ok"] is True
    assert r["note"] == "已是目标模式，无需切换"


def test_set_window_refuses_default_to_headless():
    """R-6.58：default 承载 APP 壳窗与采集窗，运行中切无头会把整个应用带走。"""
    r = asyncio.run(engine_window.set_window("default", on=False))
    assert r["ok"] is False
    assert "default 账号" in r["error"]


# ------------------------------------------------ R-6.60 close_account

def test_close_account_refuses_default():
    """default 不能单独关：关掉等于关掉整个应用。"""
    r = asyncio.run(engine_window.close_account("default"))
    assert r["ok"] is False
    assert "default 账号" in r["error"]


def test_close_account_rejects_running_task():
    """有采集任务在跑时关引擎会打断任务，必须先拒绝。"""
    server._COLLECT_TASKS["t9"] = {"status": "running", "account_id": "acc_x"}
    try:
        r = asyncio.run(engine_window.close_account("acc_x"))
    finally:
        server._COLLECT_TASKS.pop("t9", None)
    assert r["ok"] is False
    assert "采集任务进行中" in r["error"]


def test_close_account_noop_when_engine_not_running(monkeypatch):
    """没开过的账号也允许「关」，幂等返回，不报错。"""
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: None)
    r = asyncio.run(engine_window.close_account("acc_x"))
    assert r["ok"] is True
    assert "未运行" in r["note"]


def test_close_account_closes_running_engine(monkeypatch):
    """引擎在跑：真的调 pool.close，并回报已释放。"""
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: object())

    async def _close(_aid):
        return True

    monkeypatch.setattr(engine_pool.pool, "close", _close)
    r = asyncio.run(engine_window.close_account("acc_x"))
    assert r["ok"] is True
    assert "已关闭" in r["note"]
