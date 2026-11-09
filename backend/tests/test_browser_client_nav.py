"""R-6.53z8 导航规则单测（用户 2026-11-06 拍板）。

规则：URL 只允许开 /login（浏览页 /explore 仅已确认登录时可用）；其余页面只准靠
模拟鼠标/键盘或 JS 点击进入；全场景禁止 reload。本文件守护这三条不被改回去。
"""
import asyncio
import io
import pathlib

import pytest

from src import browser_client as bc


class _Mouse:
    def __init__(self, rec):
        self.rec = rec

    async def click(self, x, y):
        self.rec.append(("click", x, y))


class _Kb:
    def __init__(self, rec):
        self.rec = rec

    async def type(self, text, delay=0):
        self.rec.append(("type", text))

    async def press(self, key):
        self.rec.append(("press", key))


class _FakePage:
    """最小假页：url / 游客渲染标记 / 搜索框坐标 / 记录导航与输入。"""

    def __init__(self, url="about:blank", needs_login=False, box=None):
        self.url = url
        self.needs_login = needs_login
        self.box = box
        self.rec = []
        self.mouse = _Mouse(self.rec)
        self.keyboard = _Kb(self.rec)
        self.gotos = []

    async def evaluate(self, js, *args):
        if "placeholder" in js and "登录探索更多内容" in js:
            return self.needs_login
        # R-6.77：聚焦探针 / 全选探针要能区分出来，否则假页会把它们都当成坐标结果
        if "activeElement === el" in js:
            return self.box is not None
        if "typeof el.select" in js:
            return True
        return self.box

    async def goto(self, url, **kw):
        self.gotos.append(url)
        self.url = url


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _s(_x):
        return None
    monkeypatch.setattr(bc.asyncio, "sleep", _s)


def _run(coro):
    return asyncio.run(coro)


class TestPageNeedsLogin:
    def test_guest_render_detected(self):
        assert _run(bc._page_needs_login(_FakePage(needs_login=True))) is True

    def test_logged_in_render(self):
        assert _run(bc._page_needs_login(_FakePage(needs_login=False))) is False

    def test_eval_break_treated_as_need_login(self):
        class _Boom(_FakePage):
            async def evaluate(self, js, *args):
                raise RuntimeError("page destroyed")
        assert _run(bc._page_needs_login(_Boom())) is True


class TestEnsureBrowsePage:
    def test_on_explore_logged_in_no_navigation(self):
        p = _FakePage(url="https://www.xiaohongshu.com/explore")
        assert _run(bc._ensure_browse_page(p, "default")) is True
        assert p.gotos == []

    def test_guest_render_on_explore_goes_login(self):
        p = _FakePage(url="https://www.xiaohongshu.com/explore", needs_login=True)
        _run(bc._ensure_browse_page(p, "default"))
        assert p.gotos == [bc.LOGIN_URL]

    def test_blank_page_goes_login(self):
        p = _FakePage(url="about:blank")
        _run(bc._ensure_browse_page(p, "default"))
        assert p.gotos == [bc.LOGIN_URL]


class TestOpenLoginPage:
    def test_platform_redirect_counts_as_success(self):
        p = _FakePage()

        async def fake_goto(url, **kw):
            p.url = "https://www.xiaohongshu.com/explore"
        p.goto = fake_goto
        assert _run(bc._open_login_page(p, timeout_s=2)) is True

    def test_stuck_on_login_returns_false(self):
        p = _FakePage()
        assert _run(bc._open_login_page(p, timeout_s=2)) is False
        assert p.url == bc.LOGIN_URL


class TestSearchByTyping:
    def test_no_box_returns_false(self):
        assert _run(bc._search_by_typing(_FakePage(box=None), "翡翠", "default")) is False

    def test_focus_type_enter_and_wait(self, monkeypatch):
        """R-6.77：聚焦路径（JS focus 成功就不再坐标点击）。

        坐标点击实测会把焦点落在 BODY，输入的字进不了框、回车无效，
        页面停在 explore 首页，于是把推荐流当结果采（视频混进来）。
        """
        p = _FakePage(box={"x": 480, "y": 36})
        seen = {}

        async def fake_wait(page, timeout_s=20):
            seen["cards"] = True
            return True, ""

        async def fake_on_search(page):
            seen["search_page"] = True
            return True
        monkeypatch.setattr(bc, "_wait_note_cards", fake_wait)
        monkeypatch.setattr(bc, "_is_on_search_result", fake_on_search)
        assert _run(bc._search_by_typing(p, "翡翠", "default")) is True
        assert seen["search_page"] is True
        assert ("click", 480, 36) not in p.rec  # 聚焦成功就不走坐标点击
        assert ("press", "Backspace") in p.rec  # 清掉上次残留关键词
        assert ("type", "翡翠") in p.rec
        assert ("press", "Enter") in p.rec
        assert seen["cards"] is True


class TestPrepareSearchPageGuard:
    def test_not_logged_in_raises(self, monkeypatch):
        p = _FakePage()

        async def fake_page(_a="default"):
            return p
        monkeypatch.setattr(bc, "_page", fake_page)

        with pytest.raises(bc.BrowserLoginRequiredError):
            _run(bc._prepare_search_page("翡翠", None, "default"))


class TestNavRuleSourceGuard:
    """源码级守护：规则被改回去时立刻失败。"""

    def _src(self):
        path = pathlib.Path(bc.__file__)
        return io.open(path, encoding="utf-8").read()

    def test_no_reload_anywhere(self):
        assert "page.reload(" not in self._src()

    def test_no_direct_search_result_url(self):
        assert '"/search_result"' not in self._src()

    def test_no_direct_note_or_profile_url(self):
        src = self._src()
        assert 'f"/user/profile/' not in src
        assert "_XHS + \"/explore\"" not in src

class TestImportInjectKeepShellGuard:
    """R-6.53z8 回归：注入 Cookie 后不得关闭既有页面（壳窗被导航到 /login 时
    关掉它 → keeper「壳窗被关闭」→ 整个应用退出，用户实测踩坑）。"""

    def test_no_page_close_in_refresh(self):
        import pathlib
        src = pathlib.Path("src/server.py").read_text(encoding="utf-8")
        start = src.index("async def _refresh_engine_after_inject")
        end = src.index("async def import_cookies", start)
        body = src[start:end]
        assert ".close()" not in body, "注入后刷新不得关闭页面（会误关壳窗导致应用退出）"
        assert "reload(" not in body, "注入后刷新不得 reload"
