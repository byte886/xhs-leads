"""共享假 Playwright/CDP 基建（R-6.37c：不启动真实 Chrome、不连 9223）。

能力：FakeBrowser / FakeContext / FakePage（URL 管理、事件回调、close 状态）；
供 shell_window / engine_pool / browser_client 的特征测试注入。
只实现被测代码实际调用的最小子集，方法/属性可按需扩展。
"""
from __future__ import annotations

import asyncio
from typing import Any


class FakePage:
    """模拟 Playwright Page：url/title/close/reload/goto/evaluate/expose_function 最小子集。"""

    def __init__(self, url: str = "about:blank", title: str = ""):
        self.url = url
        self._title = title
        self.closed = False
        self._on_close = None
        self.content_text = ""
        self.html = ""
        self.extra_headers: dict[str, str] = {}
        self._eval_results: dict[str, Any] = {}
        self._on_navigate = None
        self._handlers: dict[str, list] = {}

    async def goto(self, url: str, wait_until: str = "load", timeout: int = 30000):
        self.url = url
        if self._on_navigate:
            await self._on_navigate(self)

    async def reload(self, wait_until: str = "load", timeout: int = 30000):
        pass

    async def title(self) -> str:
        return self._title

    def is_closed(self) -> bool:
        return self.closed

    async def close(self):
        self.closed = True

    async def evaluate(self, expr: str, arg: Any = None) -> Any:
        return self._eval_results.get(expr, None)

    def set_eval_result(self, expr: str, value: Any):
        self._eval_results[expr] = value

    async def set_content(self, html: str):
        self.html = html

    async def wait_for_timeout(self, ms: int):
        pass

    def on(self, event: str, callback):
        self._handlers.setdefault(event, []).append(callback)

    def emit(self, event: str):
        for cb in list(self._handlers.get(event, [])):
            cb()

    def emit_close(self):
        self.closed = True
        self.emit("close")

    def emit_crash(self):
        self.emit("crash")

    async def bring_to_front(self):
        pass

    async def set_viewport_size(self, size: dict):
        pass

    @property
    def browser(self):
        return None


class FakeContext:
    """模拟 BrowserContext：pages 列表、new_page、close、add_cookies。"""

    def __init__(self, pages: list[FakePage] | None = None):
        self.pages: list[FakePage] = pages or []
        self.closed = False
        self._cookies: list[dict] = []
        self._browser: FakeBrowser | None = None

    async def new_page(self) -> FakePage:
        p = FakePage()
        self.pages.append(p)
        return p

    async def close(self):
        self.closed = True
        for p in self.pages:
            p.closed = True

    @property
    def browser(self):
        return self._browser

    async def add_cookies(self, cookies: list[dict]):
        self._cookies.extend(cookies)

    async def cookies(self) -> list[dict]:
        return list(self._cookies)

    async def clear_cookies(self):
        self._cookies.clear()

    def grant_permissions(self, *a, **k):
        pass


class FakeBrowser:
    """模拟 Browser：is_connected / close。"""

    def __init__(self):
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self):
        self._connected = False

    async def close(self):
        self._connected = False


class FakeEngine:
    """模拟 AccountEngine：context/new_page/cookies 委托给 FakeContext/FakePage。"""

    def __init__(self, context: FakeContext | None = None):
        self._ctx = context or FakeContext()
        self.account_id = "default"
        self.closed = False
        self.cookies_list: list[dict] = []

    async def context(self) -> FakeContext:
        return self._ctx

    async def new_page(self, url: str = "about:blank") -> FakePage:
        p = await self._ctx.new_page()
        p.url = url
        return p

    async def read_cookies(self) -> list[dict]:
        return list(self.cookies_list)

    async def inject_cookies(self, cookies: list[dict]) -> bool:
        self.cookies_list = list(cookies)
        return True

    async def close(self):
        self.closed = True
        await self._ctx.close()

    async def cleanup_tabs(self, keep_task: bool = True) -> int:
        return 0
