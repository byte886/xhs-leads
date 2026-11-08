"""采集窗口关闭行为单测（R-6.73 方案 A：关窗=中断，不自动重建）。

用户口径原话：「倾向于 A，那就相当于如果关闭了，就需要重新采集。」
自动把窗口开回来属于「守护」行为，与「完全抛弃守护、一问一答」冲突，
所以这里不只测行为，还直接断言协调器上不存在任何自动重建 API——
防止这个已被否决的机制以后被悄悄加回来（历史教训：拍板移除后暗中保留）。
"""
import asyncio
import threading
import time

from src import server
from src import shell_window as sw


# ---------------------------------------------------------------- 假对象


class _Pg:
    """最简页面：只需要 is_closed 与 url。"""

    def __init__(self, url="https://www.xiaohongshu.com/explore", closed=False):
        self.url = url
        self._closed = closed

    def is_closed(self):
        return self._closed

    def on(self, *a, **k):
        return None


class _BadPg:
    """事件不支持/页面已失效的页面（验证静默降级，不抛给调用方）。"""

    def on(self, *a, **k):
        raise RuntimeError("事件不支持")

    @property
    def url(self):
        return ""


class _Task:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


class _Ctx:
    def __init__(self, cookies, fail_on=None):
        self._cookies = cookies
        self._fail = fail_on
        self.cleared = []

    async def cookies(self):
        return self._cookies

    async def clear_cookies(self, name, domain, path="/"):
        if name == self._fail:
            raise RuntimeError("单条删除失败")
        self.cleared.append(name)


class _PgCtx:
    def __init__(self, ctx):
        self.context = ctx
        self.url = ""


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        return None


def _put_task(tid, **kw):
    st = {"status": "running", "stop_event": False, "task": _Task(), "saved": 0}
    st.update(kw)
    server._COLLECT_TASKS[tid] = st
    return st


def _drop(*tids):
    for t in tids:
        server._COLLECT_TASKS.pop(t, None)


# ------------------------------------------------- 一、关窗不重建（核心）


def test_no_auto_rebuild_api_left():
    """方案 A 落地后，协调器上不允许再残留任何「自动重建」入口。"""
    for name in ("_schedule_collect_win_rebuild",
                 "_rebuild_collect_win_once",
                 "_collect_win_should_rebuild"):
        assert not hasattr(sw.WindowCoordinator, name), f"{name} 不该再存在"


def test_collect_win_close_aborts_collect(monkeypatch):
    """关采集窗 → 中止本次采集，而不是把窗口开回来。"""
    calls = []
    monkeypatch.setattr(server, "abort_collect_on_window_close",
                        lambda: calls.append(1))
    sw.WindowCoordinator()._handle_close(_Pg(), is_shell=False)
    assert calls == [1]


def test_collect_win_close_swallows_server_error(monkeypatch):
    """状态源异常时只告警，不能把关窗事件变成崩溃。"""

    def boom():
        raise RuntimeError("server 未就绪")

    monkeypatch.setattr(server, "abort_collect_on_window_close", boom)
    sw.WindowCoordinator()._handle_close(_Pg(), is_shell=False)


def test_close_ignored_when_stopping():
    """应用已在停止中 → 不再触发采集中止（避免退出流程被打扰）。"""
    calls = []
    stop = threading.Event()
    stop.set()
    c = sw.WindowCoordinator()
    c._stop = stop
    c._abort_collect_on_win_close = lambda: calls.append(1)
    c._handle_close(_Pg(), is_shell=False)
    assert calls == []


def test_shell_close_does_not_abort_collect(monkeypatch):
    """关主窗语义是「退出应用」，不能被误报成「采集窗口已关闭」。"""
    calls = []
    monkeypatch.setattr(server, "abort_collect_on_window_close",
                        lambda: calls.append(1))
    c = sw.WindowCoordinator()
    c._handle_close(_Pg(), is_shell=True)
    assert calls == []
    assert c._shell_close_ts > 0  # 仍记录时间戳供 3s 校验用


# --------------------------------------------- 二、server 侧中止与文案


def test_abort_marks_task_and_cancels():
    st = _put_task("t-abort")
    try:
        n = server.abort_collect_on_window_close()
        assert n == 1
        assert st["abort_note"] == "采集窗口已关闭，本次采集中断，请重新开始采集"
        assert st["stop_event"] is True
        assert st["task"].cancelled is True
    finally:
        _drop("t-abort")


def test_abort_ignores_finished_tasks():
    st = _put_task("t-done", status="done")
    try:
        assert server.abort_collect_on_window_close() == 0
        assert "abort_note" not in st
    finally:
        _drop("t-done")


def test_finalize_keeps_abort_note():
    """中止文案不能被覆盖成「已停止：N 条」，否则用户以为自己点了停止。"""
    st = {"saved": 3,
          "abort_note": "采集窗口已关闭，本次采集中断，请重新开始采集"}
    server._finalize_stopped(st, None, "default")
    assert st["status"] == "stopped"
    assert st["note"] == st["abort_note"]


def test_finalize_normal_note_unchanged():
    """正常停止（用户点停止）的文案不受影响。"""
    st = {"saved": 3}
    server._finalize_stopped(st, None, "default")
    assert st["note"] == "已停止：采集到 3 条"


def test_finalize_empty_stop_note():
    st = {"saved": 0}
    server._finalize_stopped(st, None, "default")
    assert st["note"] == "已停止（未采到结果）"


# ------------------------------------------------- 三、ShellWindow 薄封装


def test_evaluate_js_returns_empty_on_error():
    class _PgBoom:
        async def evaluate(self, js):
            raise RuntimeError("页面已关闭")

    w = sw.ShellWindow(_PgBoom(), "http://x")
    assert asyncio.run(w.evaluate_js("1")) == ""


def test_current_url_and_rebind():
    p = _Pg(url="https://a.b/c")
    w = sw.ShellWindow(p, "http://base///")
    assert w.base_url == "http://base"  # 尾部斜杠已归一
    assert asyncio.run(w.current_url()) == "https://a.b/c"
    w.rebind(_Pg(url="https://d.e/f"))
    assert asyncio.run(w.current_url()) == "https://d.e/f"


def test_clear_domain_cookies_filters_by_domain():
    ctx = _Ctx([{"name": "web_session", "domain": ".xiaohongshu.com", "path": "/"},
                {"name": "keep", "domain": ".example.com", "path": "/"}])
    w = sw.ShellWindow(_PgCtx(ctx), "http://x")
    assert asyncio.run(w.clear_domain_cookies("xiaohongshu")) == 1
    assert ctx.cleared == ["web_session"]


def test_clear_domain_cookies_survives_single_failure():
    ctx = _Ctx([{"name": "a", "domain": ".x.com"},
                {"name": "b", "domain": ".x.com"}], fail_on="a")
    w = sw.ShellWindow(_PgCtx(ctx), "http://x")
    assert asyncio.run(w.clear_domain_cookies("x.com")) == 1
    assert ctx.cleared == ["b"]


# --------------------------------------------- 四、协调器判活与状态


def test_alive_variants():
    assert sw.WindowCoordinator._alive(None) is None
    assert sw.WindowCoordinator._alive(_Pg(closed=True)) is None

    class _Boom:
        def is_closed(self):
            raise RuntimeError("page 对象异常")

    assert sw.WindowCoordinator._alive(_Boom()) is None
    p = _Pg()
    assert sw.WindowCoordinator._alive(p) is p


def test_register_and_lookup():
    c = sw.WindowCoordinator()
    p = _Pg()
    c.register_shell(p)
    c.register_collect_win(p)
    assert c.shell() is p
    assert c.collect_win() is p


def test_shell_close_age_default_and_after_record():
    c = sw.WindowCoordinator()
    assert c.shell_close_age() > 1e17  # 无记录=极大值，keeper 据此不误判
    c._shell_close_ts = time.monotonic() - 2.0
    assert 1.8 < c.shell_close_age() < 3.0


def test_bind_without_running_loop():
    c = sw.WindowCoordinator()
    c.bind(None, None)
    assert c._loop is None  # 同步上下文没有 running loop，靠轮询兜底


def test_bind_page_events_silent_when_unsupported():
    sw.WindowCoordinator._bind_page_events(_BadPg(), lambda: None, lambda: None)


def test_engine_alive_when_not_attached():
    assert asyncio.run(sw.WindowCoordinator()._engine_alive_after_close()) is True


def test_engine_alive_when_pool_errors():
    class _PoolBoom:
        async def get(self, k="default"):
            raise RuntimeError("引擎未就绪")

    c = sw.WindowCoordinator()
    c.attach_engine(_PoolBoom())
    assert asyncio.run(c._engine_alive_after_close()) is True


def test_engine_alive_when_account_missing():
    class _PoolNone:
        async def get(self, k="default"):
            return None

    c = sw.WindowCoordinator()
    c.attach_engine(_PoolNone())
    assert asyncio.run(c._engine_alive_after_close()) is True


# --------------------------------------------- 五、助手函数与模块单例


def test_json_body_parses(monkeypatch):
    monkeypatch.setattr(sw.urllib.request, "urlopen",
                        lambda url, timeout: _Resp(b'{"ok": true}'))
    assert sw._json_body("http://x", 3) == {"ok": True}


def test_json_post_sends_json_payload(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["data"] = req.data
        return _Resp(b'{"ok": 1}')

    monkeypatch.setattr(sw.urllib.request, "urlopen", fake)
    assert sw._json_post("http://x", {"a": 1}, 3) == {"ok": 1}
    assert b'"a": 1' in seen["data"]


def test_get_coordinator_singleton():
    assert sw.get_coordinator() is sw.get_coordinator()


def test_current_shell_page_none_before_keeper(monkeypatch):
    monkeypatch.setattr(sw, "_COORD", None)
    assert sw.current_shell_page() is None


def test_keeper_collect_target_is_login_page():
    assert asyncio.run(
        sw._keeper_collect_target_url(None, None)) == sw._LOGIN_PAGE_URL
