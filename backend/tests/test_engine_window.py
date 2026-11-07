"""引擎窗口开关（set_window）的守卫行为。

原「事件转发」相关测试随能力下线一并删除（R-6.53z8p）。
"""
import asyncio

from src import config, engine_window, server


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
