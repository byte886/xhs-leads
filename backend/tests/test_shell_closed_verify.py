"""壳窗关闭判定单测（R-6.53z8d：轮询下线后，退出语义由 close 事件唯一承载）。

「关主窗 = 关闭应用」的判定入口从 5s 兜底轮询迁到 close 事件的 3s 校验，
三种结局必须逐条守住：引擎存活才退出、崩溃期不退出、已停止不重复退出。
"""
import asyncio

from src import shell_window as sw


class _Eng:
    """假账号引擎：rebuild_age / is_connected_now 决定「是否崩溃恢复期」。"""

    def __init__(self, age=999.0, connected=True):
        self._age = age
        self._conn = connected

    def rebuild_age(self):
        return self._age

    def is_connected_now(self):
        return self._conn

    async def get(self, key="default"):
        return self


def _coord(engine=None, stop=None, closed=None):
    c = sw.WindowCoordinator()
    c.bind(stop, (lambda: closed.append(1)) if closed is not None else None)
    if engine is not None:
        c.attach_engine(engine)
    return c


def test_closed_triggers_exit_when_engine_alive():
    """引擎存活且非重建期 → 用户真关主窗，触发退出。"""
    closed = []
    asyncio.run(_coord(closed=closed)._shell_closed_verify())
    assert closed == [1]


def test_closed_skips_when_engine_rebuilding():
    """引擎 60s 内重建过（崩溃恢复）→ 跳过退出，不误杀应用。"""
    closed = []
    asyncio.run(_coord(_Eng(age=10), closed=closed)._shell_closed_verify())
    assert closed == []


def test_closed_skips_when_engine_disconnected():
    """引擎连接已断开（崩溃）→ 跳过退出。"""
    closed = []
    asyncio.run(_coord(_Eng(connected=False), closed=closed)._shell_closed_verify())
    assert closed == []


def test_closed_skips_after_stop():
    """应用已在停止中 → 不重复触发退出。"""
    import threading

    closed = []
    stop = threading.Event()
    stop.set()
    asyncio.run(_coord(stop=stop, closed=closed)._shell_closed_verify())
    assert closed == []
