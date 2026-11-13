"""风控错误页（300013）识别与退登处置的回归测试。

2026-11-13 真机踩到的坑：错误页 URL 是
`/website-login/error?redirectPath=https://www.xiaohongshu.com/explore/xxx`
—— `redirectPath` 参数里带着 `/explore/`，旧的位置判定先命中 `/explore/`，
把错误页错判成"详情页"，于是"被限制"被当成"详情页打开了"吞掉，
约定的「退登 → 回登录页」永远不会执行。本文件守护该判定顺序。
"""
import asyncio

import pytest

from src import browser_client as bc


ERR_URL = (
    "https://www.xiaohongshu.com/website-login/error?redirectPath="
    "https://www.xiaohongshu.com/explore/69f0961b0000000038035ef2?xsec_token=ABMj&"
    "error_code=300013&error_msg=%E6%A3%80%E5%8D%E8%9C%80&verifyMsg="
)


class _Page:
    def __init__(self, url):
        self.url = url
        self.moved = []

    async def goto(self, url, **_):
        self.moved.append(url)
        self.url = url

    async def evaluate(self, _):
        return {}


def test_error_page_wins_over_explore_in_redirect_path():
    """错误页必须判成 error，不能被 redirectPath 里的 /explore/ 抢成 detail。"""
    assert bc._page_position(_Page(ERR_URL)) == "error"


def test_error_page_without_redirect_path():
    assert bc._page_position(_Page("https://www.xiaohongshu.com/website-login/error?error_code=300013")) == "error"


def test_normal_detail_page_still_detail():
    """正常详情页（无 redirectPath）仍判 detail，别把功能修坏。"""
    assert bc._page_position(_Page("https://www.xiaohongshu.com/explore/abc123?xsec_token=x")) == "detail"


def test_drain_comments_raises_on_error_page():
    """评论采集入口看到错误页必须抛 _RiskLimited，而不是静默返回空列表。"""
    with pytest.raises(bc._RiskLimited):
        asyncio.run(bc._drain_comments(_Page(ERR_URL)))


def test_risk_relogin_clears_cookies_then_navigates(monkeypatch):
    """退登处置：先清 cookie（= 退出登录），再导航登录页，最后抛登录要求。"""
    calls = []

    class _Ctx:
        async def clear_cookies(self):
            calls.append("clear")

    page = _Page(ERR_URL)
    page.context = _Ctx()

    async def fake_force(p, account_id):
        await p.context.clear_cookies()
        await p.goto(bc.LOGIN_URL)
        calls.append("goto_login")

    def fake_wall(account_id, reason):
        calls.append("wall")

    monkeypatch.setattr(bc, "_force_relogin", fake_force)
    monkeypatch.setattr(bc, "_handle_login_wall", fake_wall)

    with pytest.raises(bc.BrowserLoginRequiredError):
        asyncio.run(bc._risk_relogin(page, "default", "note-1"))

    assert calls == ["clear", "goto_login", "wall"]
