"""兜底轮询单轮（_keeper_poll_pass）编排单测。

R-6.22p 的硬承诺是「关主窗=关闭应用」；这条承诺只由 keeper 判定，此前无
任何用例——拆分成 snapshot/shell_pass/missing_shell/tab2_pass 后逐路径守护。
"""
import asyncio
from types import SimpleNamespace

from src import shell_window as sw


def _run(coro):
    return asyncio.run(coro)


class _FakeEng:
    def __init__(self, rebuild_age=999.0, connected=True):
        self._age = rebuild_age
        self._conn = connected

    def rebuild_age(self):
        return self._age

    def is_connected_now(self):
        return self._conn

    async def context(self):
        return {"pages": []}


class _FakePool:
    def __init__(self, eng):
        self._eng = eng

    async def get(self, name):
        return self._eng


class _FakeCoord:
    def __init__(self, close_age=1e18, shell=None):
        self._close_age = close_age
        self._shell = shell
        self.registered = []

    def shell(self):
        return self._shell

    def shell_close_age(self):
        return self._close_age

    def register_shell(self, s):
        self._shell = s
        self.registered.append(s)

    def watch(self, p, is_shell=False):
        pass


def _patch(monkeypatch, *, collecting=False, shell_from_find=None):
    monkeypatch.setattr(sw, "_server_has_running_collect", lambda: collecting)
    monkeypatch.setattr(sw, "_keeper_log_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(sw, "_keeper_shell_effective", lambda coord, ctx: None)

    async def fake_find(ctx, base, coord):
        return shell_from_find

    monkeypatch.setattr(sw, "_keeper_find_shell", fake_find)
    tab2 = []

    async def fake_tab2(*a, **k):
        tab2.append(1)

    monkeypatch.setattr(sw, "_keeper_tab2_pass", fake_tab2)
    return tab2


def _call(coord, eng, on_shell_closed):
    return sw._keeper_poll_pass("http://127.0.0.1:8791", coord, None,
                                on_shell_closed, {}, SimpleNamespace(pool=_FakePool(eng)),
                                [frozenset()])


class TestKeeperPollPass:
    def test_engine_alive_main_window_closed_exits(self, monkeypatch):
        tab2 = _patch(monkeypatch, shell_from_find=None)
        closed = []
        coord, eng = _FakeCoord(close_age=1), _FakeEng(rebuild_age=999, connected=True)
        rc = _run(_call(coord, eng, lambda: closed.append(1)))
        assert rc == "exit"
        assert closed == [1]
        assert tab2 == []  # 退出前不做 tab2 治理

    def test_no_close_signal_and_no_callback_does_not_raise(self, monkeypatch):
        _patch(monkeypatch, shell_from_find=None)
        coord, eng = _FakeCoord(close_age=1e18), _FakeEng(connected=False)
        rc = _run(_call(coord, eng, None))
        assert rc == "continue"

    def test_rebuild_window_reopens_shell(self, monkeypatch):
        tab2 = _patch(monkeypatch, shell_from_find=None)
        new_shell = object()

        async def fake_rebuild(ctx, base, coord, pool):
            return new_shell

        monkeypatch.setattr(sw, "_keeper_rebuild_shell", fake_rebuild)
        coord, eng = _FakeCoord(close_age=1e18), _FakeEng(rebuild_age=30)
        rc = _run(_call(coord, eng, lambda: None))
        assert rc == "continue"
        # 原实现语义：重开的壳窗本轮只打印、不注册，由下一轮 _keeper_find_shell
        # 扫到后补注册（watch 由此启动）。此处按现状断言，行为变更需另行拍板。
        assert coord.registered == []
        assert tab2 == [1]

    def test_rebuild_not_ready_does_not_exit(self, monkeypatch):
        _patch(monkeypatch, shell_from_find=None)

        async def fake_rebuild(*a, **k):
            raise RuntimeError("engine not ready")

        monkeypatch.setattr(sw, "_keeper_rebuild_shell", fake_rebuild)
        closed = []
        coord, eng = _FakeCoord(close_age=1e18), _FakeEng(rebuild_age=30)
        rc = _run(_call(coord, eng, lambda: closed.append(1)))
        assert rc == "continue"  # 重建期未就绪不退出，下轮重试
        assert closed == []

    def test_collecting_skips_tab2_governance(self, monkeypatch):
        tab2 = _patch(monkeypatch, collecting=True, shell_from_find=object())
        coord, eng = _FakeCoord(close_age=1e18), _FakeEng()
        rc = _run(_call(coord, eng, lambda: None))
        assert rc == "continue"
        assert tab2 == []  # 采集执行中暂停 tab2 治理（避免 CDP 并发）


class _EventPage:
    """可挂事件的假 page：记录 (event, handler) 并支持手动触发。"""

    def __init__(self, url="http://127.0.0.1:8791/"):
        self.url = url
        self.handlers = {}
        self.closed = False

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def is_closed(self):
        return self.closed

    def fire(self, event):
        for h in self.handlers.get(event, []):
            h()


class TestWatchCallbacks:
    """watch：幂等挂载 + close/crash 回调分流（壳窗 vs 采集窗）。"""

    def test_watch_is_idempotent(self):
        coord, page = sw.WindowCoordinator(), _EventPage()
        coord.watch(page, is_shell=True)
        n = len(page.handlers["close"])
        coord.watch(page, is_shell=True)
        assert len(page.handlers["close"]) == n  # 同一 page 只挂一次
        assert "crash" in page.handlers

    def test_shell_close_records_timestamp(self):
        coord, page = sw.WindowCoordinator(), _EventPage()
        coord.watch(page, is_shell=True)
        before = coord.shell_close_age()
        page.fire("close")
        assert coord.shell_close_age() < before  # 记录时间戳 → age 由极大值变小
        assert coord.shell_close_age() < 1e18

    def test_tab2_close_does_not_record(self):
        coord, page = sw.WindowCoordinator(), _EventPage()
        coord.watch(page, is_shell=False)
        page.fire("close")
        assert coord.shell_close_age() >= 1e18  # 采集窗关闭不影响「主窗已关」判定

    def test_stopped_coordinator_ignores_events(self):
        import threading

        coord, page = sw.WindowCoordinator(), _EventPage()
        stop = threading.Event()
        stop.set()
        coord.bind(stop, lambda: None)
        coord.watch(page, is_shell=True)
        page.fire("close")
        assert coord.shell_close_age() >= 1e18  # 已停止：不再记时间戳

    def test_crash_only_schedules_for_shell(self, monkeypatch):
        calls = []

        async def fake_cleanup(p):
            calls.append(p)

        async def scenario():
            coord, page = sw.WindowCoordinator(), _EventPage()
            monkeypatch.setattr(coord, "_recovery_cleanup_safe", fake_cleanup)
            monkeypatch.setattr(sw.asyncio, "get_event_loop",
                                lambda: SimpleNamespace(call_later=lambda d, fn: fn()))
            coord.watch(page, is_shell=True)
            page.fire("crash")
            await asyncio.sleep(0)
            coord2, p2 = sw.WindowCoordinator(), _EventPage()
            monkeypatch.setattr(coord2, "_recovery_cleanup_safe", fake_cleanup)
            coord2.watch(p2, is_shell=False)
            p2.fire("crash")
            await asyncio.sleep(0)

        _run(scenario())
        assert len(calls) == 1  # 只壳窗崩溃触发恢复清理；采集窗崩溃不触发
