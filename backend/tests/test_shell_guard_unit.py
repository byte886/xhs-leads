"""登录守卫纯函数与循环契约单测（R-6.48 系列）。

守卫是「登录闭环」的唯一驱动者：触发来源（事件/轮询）、逃逸拦截、监视单步、
循环收尾（移除导航拦截）任何一处判错都会让用户卡在登录页或误跳转。此前只有
UI 自动化能覆盖，这里锁住分支语义。
"""
import asyncio
import threading

from src import shell_window as sw


def _run(coro):
    return asyncio.run(coro)


async def _noop_sleep(_s):
    return None


class _FakeFrame:
    def __init__(self, url, main=True, broken=False):
        self._url = url
        self._main = main
        self._broken = broken

    @property
    def url(self):
        return self._url

    def is_main_frame(self):
        if self._broken:
            raise RuntimeError("frame destroyed")
        return self._main


class _FakeWin:
    page = None

    async def cookies(self):
        return []

    async def current_url(self):
        return "https://www.xiaohongshu.com/login"

    async def load_url(self, url, **k):
        self.loaded = url

    async def evaluate_js(self, js):
        return None


class TestGuardDetectEscape:
    def test_escape_returns_url(self):
        stop = threading.Event()
        url = "https://www.xiaohongshu.com/explore?x=1"
        assert sw._guard_detect_escape(_FakeFrame(url), stop, "xiaohongshu.com") == url

    def test_login_page_not_escape(self):
        stop = threading.Event()
        assert sw._guard_detect_escape(
            _FakeFrame("https://www.xiaohongshu.com/login"), stop, "xiaohongshu.com") is None

    def test_sub_frame_ignored(self):
        stop = threading.Event()
        assert sw._guard_detect_escape(
            _FakeFrame("https://www.xiaohongshu.com/explore", main=False), stop,
            "xiaohongshu.com") is None

    def test_non_xhs_domain_ignored(self):
        stop = threading.Event()
        assert sw._guard_detect_escape(
            _FakeFrame("https://example.com/page"), stop, "xiaohongshu.com") is None

    def test_stopped_guard_ignored(self):
        stop = threading.Event()
        stop.set()
        assert sw._guard_detect_escape(
            _FakeFrame("https://www.xiaohongshu.com/explore"), stop, "xiaohongshu.com") is None

    def test_destroyed_frame_ignored(self):
        stop = threading.Event()
        assert sw._guard_detect_escape(_FakeFrame("", broken=True), stop, "xiaohongshu.com") is None


class TestGuardNextForce:
    def test_event_driven_returns_force_and_clears(self):
        evt = asyncio.Event()
        evt.set()
        gate = {"evt": evt, "pending": True, "force": True}
        assert _run(sw._guard_next_force(gate, _FakeWin(), "b", threading.Event())) is True
        assert evt.is_set() is False  # 消费后必须清位，否则下轮空转

    def test_cancelled_pending_returns_none(self):
        evt = asyncio.Event()
        evt.set()
        gate = {"evt": evt, "pending": False, "force": True}
        assert _run(sw._guard_next_force(gate, _FakeWin(), "b", threading.Event())) is None

    def test_polling_fallback(self, monkeypatch):
        monkeypatch.setattr(sw.asyncio, "sleep", _noop_sleep)

        async def fake_wait(win, base, stop):
            return False  # 主动重登

        monkeypatch.setattr(sw, "_guard_wait_pending", fake_wait)
        assert _run(sw._guard_next_force(None, _FakeWin(), "b", threading.Event())) is False


class TestGuardMonitorStep:
    def test_forced_selected(self, monkeypatch):
        used = []

        async def forced(win, base, cs, state):
            used.append("forced")
            return None

        async def voluntary(win, base, cs, state):
            used.append("voluntary")
            return None

        monkeypatch.setattr(sw, "_guard_monitor_forced", forced)
        monkeypatch.setattr(sw, "_guard_monitor_voluntary", voluntary)
        _run(sw._guard_monitor_step(_FakeWin(), "b", True, {"import": 0.0}))
        assert used == ["forced"]

    def test_voluntary_selected(self, monkeypatch):
        used = []

        async def forced(win, base, cs, state):
            used.append("forced")
            return None

        async def voluntary(win, base, cs, state):
            used.append("voluntary")
            return None

        monkeypatch.setattr(sw, "_guard_monitor_forced", forced)
        monkeypatch.setattr(sw, "_guard_monitor_voluntary", voluntary)
        _run(sw._guard_monitor_step(_FakeWin(), "b", False, {"import": 0.0}))
        assert used == ["voluntary"]

    def test_cookies_read_error_degrades_to_empty(self, monkeypatch):
        got = []

        async def forced(win, base, cs, state):
            got.append(cs)
            return None

        class _BadWin(_FakeWin):
            async def cookies(self):
                raise RuntimeError("cookies unavailable")

        monkeypatch.setattr(sw, "_guard_monitor_forced", forced)
        _run(sw._guard_monitor_step(_BadWin(), "b", True, {"import": 0.0}))
        assert got == [[]]


class TestGuardMonitorLoop:
    def test_success_returns_true_and_removes_hook(self, monkeypatch):
        removed = []

        async def fake_rebind(win, base):
            return False

        async def fake_step(win, base, force, state):
            return True

        monkeypatch.setattr(sw, "_rebind_if_closed", fake_rebind)
        monkeypatch.setattr(sw, "_guard_monitor_step", fake_step)
        monkeypatch.setattr(sw, "_guard_hook_forced_nav",
                            lambda win, stop: (lambda: removed.append(1)))
        assert _run(sw._guard_monitor_loop(_FakeWin(), "b", threading.Event(), True)) is True
        assert removed == [1]  # finally 必须移除监听，避免叠加

    def test_stop_before_loop_returns_false(self, monkeypatch):
        stop = threading.Event()
        stop.set()
        removed = []
        monkeypatch.setattr(sw, "_guard_hook_forced_nav",
                            lambda win, s: (lambda: removed.append(1)))
        assert _run(sw._guard_monitor_loop(_FakeWin(), "b", stop, True)) is False
        assert removed == [1]

    def test_no_hook_for_voluntary_login(self, monkeypatch):
        hooked = []

        async def fake_rebind(win, base):
            return False

        async def fake_step(win, base, force, state):
            return False

        monkeypatch.setattr(sw, "_rebind_if_closed", fake_rebind)
        monkeypatch.setattr(sw, "_guard_monitor_step", fake_step)
        monkeypatch.setattr(sw, "_guard_hook_forced_nav",
                            lambda win, stop: hooked.append(1))
        stop = threading.Event()
        stop.set()
        assert _run(sw._guard_monitor_loop(_FakeWin(), "b", stop, False)) is False
        assert hooked == []  # 主动重登不锁页面
