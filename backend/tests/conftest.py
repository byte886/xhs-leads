"""共享 fixtures：临时数据库/图片目录（不污染真实 data/）+ 全局禁用真实浏览器连接。"""
import datetime
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """全局兜底：任何测试都不得真实连接 Chrome/启动 playwright driver。

    背景（挂起根因）：FastAPI `@app.on_event("startup")` 注册的是原始函数对象，
    测试里 `monkeypatch.setattr(server, "_startup_cleanup", ...)` 只改模块属性、对
    事件表无效 → TestClient 启动时仍真实执行 cleanup_tabs → 连接 9223 + 启动
    playwright 子进程。全量运行时多个 driver 子进程泄漏 → pytest 退出收尾挂起
    （主线程等锁 + waitpid 等子进程）。这里全局 patch browser_client.cleanup_tabs
    为 noop，从根上切断真实浏览器调用。
    """
    from src import browser_client

    async def _noop_cleanup_tabs():
        return None

    monkeypatch.setattr(browser_client, "cleanup_tabs", _noop_cleanup_tabs)


@pytest.fixture(autouse=True)
def _isolate_real_data(monkeypatch, tmp_path):
    """全局兜底：测试绝不读写真实 data/（尤其不许清空用户已采集的笔记）。

    背景（真实事故）：`with TestClient(app)` 会触发 @app.on_event("startup")
    → _reset_collection_data() 把 notes / 采集上下文 / 采集图片清掉（客户库保留）。该函数只经
    `_db()`（读模块级 DB_PATH）与 IMAGES_DIR 落手，所以把这两者指向临时目录后，
    startup 的清理与测试自己的「清库」用例都只作用于临时数据，真实 data/ 不受影响。
    """
    from src import server

    monkeypatch.setattr(server, "DB_PATH", tmp_path / "isolated.db")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "images")


@pytest.fixture(autouse=True)
def _reset_collect_throttle():
    """重置拟人频率保护状态：测试间连续调用 /api/search|collect_start 不应互相节流。

    架构改造 P1 后节流为 risk 每账号状态（server._last_collect_end_at 已删除），
    清空 risk._STATES 等价于重置全部账号节流计时。
    """
    from src import risk

    risk._STATES.clear()


@pytest.fixture()
def now_utc():
    return datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture()
def tmp_db(tmp_path):
    """返回一个初始化了 notes/publish_logs 表的 sqlite 连接（临时文件）。"""
    from src.server import _init_tables
    con = sqlite3.connect(tmp_path / "test.db")
    con.row_factory = sqlite3.Row
    _init_tables(con)
    yield con
    con.close()


@pytest.fixture()
def tmp_images(tmp_path, monkeypatch):
    """把 IMAGES_DIR 指向临时目录（清理逻辑测试用）。"""
    from src import server
    d = tmp_path / "images"
    d.mkdir()
    monkeypatch.setattr(server, "IMAGES_DIR", d)
    return d
