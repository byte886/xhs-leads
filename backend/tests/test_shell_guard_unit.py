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

    def test_no_gate_blocks_until_stop(self):
        """R-6.53z8h：无事件源不再轮询查 HTTP，只阻塞到 stop；唤醒后返回 None。"""
        stop = threading.Event()
        stop.set()
        assert _run(sw._guard_next_force(None, _FakeWin(), "b", stop)) is None


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
    """R-6.72：登录监视循环不再注册任何导航拦截 hook（自动拉回已整体删除）。

    旧契约「finally 必须移除 framenavigated 监听」随实现一起废弃；现在只需
    保证：登录成功 → True 结束；已停止/未登录 → False。同时用断言锁住
    「不再挂 framenavigated 监听」，防止旧自激逻辑被悄悄加回。
    """

    def test_success_returns_true(self, monkeypatch):
        async def fake_rebind(win, base):
            return False

        async def fake_burst(win, base, force, state):
            return True

        monkeypatch.setattr(sw, "_rebind_if_closed", fake_rebind)
        monkeypatch.setattr(sw, "_guard_verify_burst", fake_burst)
        assert _run(sw._guard_monitor_loop(_FakeWin(), "b", threading.Event(), True)) is True

    def test_stop_before_loop_returns_false(self, monkeypatch):
        stop = threading.Event()
        stop.set()

        async def fake_burst(win, base, force, state):
            return None

        monkeypatch.setattr(sw, "_guard_verify_burst", fake_burst)
        assert _run(sw._guard_monitor_loop(_FakeWin(), "b", stop, True)) is False

    def test_only_event_wake_sources_registered(self, monkeypatch):
        """强制模式也只挂事件唤醒源，不得加回任何拉回钩子（自激源已除）。

        framenavigated/close 是**唤醒**事件（状态可能变化时才验证一次），
        不是「离开登录页就 load_url 拉回」的触发源——后者已随 R-6.72 整体删除。
        若有人新增第三个监听，说明绕道把拉回逻辑加回来了，此断言会失败。
        """
        events = []

        class _Page:
            def on(self, event, cb):
                events.append(event)

            def remove_listener(self, event, cb):
                pass

        class _Win(_FakeWin):
            page = _Page()

        async def fake_burst(win, base, force, state):
            return None

        monkeypatch.setattr(sw, "_guard_verify_burst", fake_burst)
        stop = threading.Event()
        stop.set()
        _run(sw._guard_monitor_loop(_Win(), "b", stop, True))
        assert sorted(events) == ["close", "framenavigated"]

