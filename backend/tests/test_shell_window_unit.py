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
