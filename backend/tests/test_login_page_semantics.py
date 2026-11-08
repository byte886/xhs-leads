"""登录页语义测试（R-6.72）。

用户口径：「如果右边的窗口还没有登录，那就直接返回还未登录，不要等来等去」。
引擎页停在 /login 时，`_engine_login_state` 会把它算进 kicked（防 cookie 残留
假阳性放行），但文案不能说成「被平台踢出」——那是两个不同的事实。
"""
import asyncio

from src import server


class _Page:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False


class _Ctx:
    def __init__(self, urls):
        self.pages = [_Page(u) for u in urls]


class _Eng:
    def __init__(self, urls=None, boom=False):
        self._urls = urls or []
        self._boom = boom

    async def context(self):
        if self._boom:
            raise RuntimeError("engine not ready")
        return _Ctx(self._urls)


class TestLoginPageSemantics:
    def test_login_page_is_detected(self):
        assert asyncio.run(
            server._engine_on_login_page(_Eng(["https://www.xiaohongshu.com/login"]))
        ) is True

    def test_explore_page_is_not_login(self):
        assert asyncio.run(
            server._engine_on_login_page(_Eng(["https://www.xiaohongshu.com/explore"]))
        ) is False

    def test_host_page_ignored(self):
        """只剩宿主前端页（无小红书页）时不判登录页，避免误改被踢文案。"""
        assert asyncio.run(
            server._engine_on_login_page(_Eng(["http://127.0.0.1:8791/"]))
        ) is False

    def test_context_error_returns_false(self):
        assert asyncio.run(
            server._engine_on_login_page(_Eng(boom=True))
        ) is False
