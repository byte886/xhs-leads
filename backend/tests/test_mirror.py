"""mirror.send_event 特征测试（R-6.36b：事件转发分支现行行为固化；假 page，无真浏览器）。"""
import asyncio

import pytest

from src import server  # noqa: F401（先导入 server 打破 mirror→engine_pool 循环导入）
from src import mirror


class _FakeMouse:
    def __init__(self):
        self.calls = []
    async def click(self, x, y):
        self.calls.append(("click", x, y))
    async def wheel(self, dx, dy):
        self.calls.append(("wheel", dx, dy))
    async def move(self, x, y):
        self.calls.append(("move", x, y))
    async def down(self):
        self.calls.append(("down",))
    async def up(self):
        self.calls.append(("up",))


class _FakeKeyboard:
    def __init__(self):
        self.typed = []
    async def type(self, text, delay=0):
        self.typed.append((text, delay))


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()


@pytest.fixture()
def fake_page(monkeypatch):
    pg = _FakePage()
    async def _login_page(acc):
        return pg
    monkeypatch.setattr(mirror, "login_page", _login_page)
    return pg


def test_send_click(fake_page):
    r = asyncio.run(mirror.send_event("default", {"type": "click", "x": 100, "y": 200}))
    assert r == {"ok": True}
    assert fake_page.mouse.calls == [("click", 100, 200)]


def test_send_type(fake_page):
    r = asyncio.run(mirror.send_event("default", {"type": "type", "text": "hi"}))
    assert r["ok"] is True
    assert fake_page.keyboard.typed == [("hi", 30)]  # 拟人节奏 30ms/字符


def test_send_scroll_defaults(fake_page):
    r = asyncio.run(mirror.send_event("default", {"type": "scroll"}))
    assert r["ok"] is True
    assert fake_page.mouse.calls == [("wheel", 0, 0)]


def test_send_drag_segmented(fake_page):
    r = asyncio.run(mirror.send_event("default", {"type": "drag", "x1": 0, "y1": 0, "x2": 10, "y2": 20}))
    assert r["ok"] is True
    kinds = [c[0] for c in fake_page.mouse.calls]
    # move + down + 5 段 move + up
    assert kinds.count("move") == 6 and "down" in kinds and "up" in kinds


def test_send_unknown_type(fake_page):
    r = asyncio.run(mirror.send_event("default", {"type": "pinch"}))
    assert r["ok"] is False and "不支持" in r["error"]
