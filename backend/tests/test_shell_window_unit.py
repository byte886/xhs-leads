"""shell_window 纯助手特征测试（R-6.37c：tab 关闭/安全关闭/JSON 助手）。"""
import asyncio

from src import shell_window as sw
from tests.fake_playwright import FakeContext, FakePage


def test_safe_close_closes():
    p = FakePage()
    asyncio.run(sw._safe_close(p))
    assert p.is_closed()


def test_safe_close_ignores_error():
    class Boom:
        async def close(self):
            raise RuntimeError("x")
    asyncio.run(sw._safe_close(Boom()))  # 不抛即可


def test_close_matching_tabs():
    ctx = FakeContext()
    keep = FakePage("about:blank")
    close_me = FakePage("https://example.com/close")
    stay = FakePage("https://example.com/keep")
    ctx.pages = [keep, close_me, stay]

    asyncio.run(sw._close_matching_tabs(ctx, keep, lambda p: "close" in p.url))
    assert close_me.is_closed()
    assert not keep.is_closed()
    assert not stay.is_closed()


def test_close_matching_skips_closed():
    ctx = FakeContext()
    p = FakePage("https://x.com")
    p.closed = True
    ctx.pages = [p]
    asyncio.run(sw._close_matching_tabs(ctx, None, lambda x: True))
    # 已关闭不重复操作


# ---------------- R-6.48o：崩溃恢复残留清理 ----------------

def test_sweep_blank_closes_newtab_residue():
    """引擎崩溃后 Chrome 会话恢复会还原 chrome://newtab 默认页——必须清理。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    residue = [
        FakePage("chrome://newtab"),
        FakePage("chrome://newtab/"),
        FakePage("about:blank"),
        FakePage(""),
    ]
    ctx.pages = [shell, *residue]
    asyncio.run(sw._keeper_sweep_blank(ctx, shell))
    assert all(p.is_closed() for p in residue), "newtab/blank 残留必须全部关闭"
    assert not shell.is_closed(), "壳窗必须保留"


def test_sweep_blank_keeps_working_pages():
    """正常运行的页面（explore/login-hint/任务页）不被 sweep 误关。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    tab2 = FakePage("http://127.0.0.1:8791/login-hint")
    explore = FakePage("https://www.xiaohongshu.com/explore")
    ctx.pages = [shell, tab2, explore]
    asyncio.run(sw._keeper_sweep_blank(ctx, shell))
    assert not tab2.is_closed() and not explore.is_closed()


def test_recovery_cleanup_closes_all_non_shell():
    """崩溃恢复：只保留主窗口，其余（调试窗 Tab2/残留）全部关闭。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    tab2 = FakePage("http://127.0.0.1:8791/login-hint")
    newtab = FakePage("chrome://newtab")
    ctx.pages = [shell, tab2, newtab]
    n = asyncio.run(sw._keeper_recovery_cleanup(ctx, shell))
    assert n == 2
    assert tab2.is_closed() and newtab.is_closed()
    assert not shell.is_closed()


def test_recovery_cleanup_skips_closed_and_shell():
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    dead = FakePage("http://127.0.0.1:8791/login-hint")
    dead.closed = True
    ctx.pages = [shell, dead]
    n = asyncio.run(sw._keeper_recovery_cleanup(ctx, shell))
    assert n == 0


# ---------------- R-6.48o：壳窗引用失效判定（崩溃/CDP 断连盲区） ----------------

class _FakeCoord:
    def __init__(self, s):
        self._s = s

    def shell(self):
        return self._s


def test_shell_effective_alive():
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    ctx.pages = [shell]
    assert sw._keeper_shell_effective(_FakeCoord(shell), ctx) is shell


def test_shell_effective_none_when_none():
    assert sw._keeper_shell_effective(_FakeCoord(None), FakeContext()) is None


def test_shell_effective_none_when_closed():
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    shell.closed = True
    ctx.pages = [shell]
    assert sw._keeper_shell_effective(_FakeCoord(shell), ctx) is None


def test_shell_effective_none_when_session_rebuilt():
    """引擎崩溃/CDP 断连：page 不标记 closed 但引用已不属于新会话页面集 → 判失效。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")  # 旧会话引用
    ctx.pages = [FakePage("https://www.xiaohongshu.com/login?redirectPath=1")]  # 新会话页面
    assert sw._keeper_shell_effective(_FakeCoord(shell), ctx) is None


# ---------------- R-6.48o：壳窗口崩溃（渲染器）→ 恢复清理 ----------------

def test_recovery_cleanup_safe_keeps_crashed_shell():
    """壳窗口崩溃：崩溃页（传入引用）保留由 Chrome 恢复，其余窗口关闭。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    tab2 = FakePage("http://127.0.0.1:8791/login-hint")
    newtab = FakePage("chrome://newtab")
    ctx.pages = [shell, tab2, newtab]
    shell.context = ctx
    coord = sw.WindowCoordinator()
    asyncio.run(coord._recovery_cleanup_safe(shell))
    assert tab2.is_closed() and newtab.is_closed()
    assert not shell.is_closed()


def test_watch_shell_mounts_crash_handler():
    """watch 挂载 crash 监听：壳窗渲染器崩溃 → 触发恢复清理调度（不炸、幂等）。"""
    ctx = FakeContext()
    shell = FakePage("https://www.xiaohongshu.com/login?redirectPath=1")
    tab2 = FakePage("http://127.0.0.1:8791/login-hint")
    ctx.pages = [shell, tab2]
    coord = sw.WindowCoordinator()
    coord.bind(None, None)
    coord.watch(shell, is_shell=True)
    coord.watch(shell, is_shell=True)  # 幂等：重复挂载不重复注册
    # 无异常 + 第二次 watch 未重复注册（_watched 大小保持 1）
    assert len(coord._watched) == 1
