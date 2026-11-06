"""浏览器客户端纯逻辑单测：滚动采集终止判定 + URL/验证页判定 + CDP 路径可移植性。"""
import asyncio
import os
import sys

from src import browser_client as bc
from src import config
from src import engine_types as etc


class TestShouldStopCollect:
    def test_count_reached(self):
        assert bc._should_stop_collect(30, 30, 0, 5, None) == (True, 0)

    def test_user_stop(self):
        assert bc._should_stop_collect(5, 30, 0, 1, lambda: True) == (True, 0)

    def test_stale_increments(self):
        stop, stale = bc._should_stop_collect(0, 30, 1, 0, None)
        assert stop is False and stale == 2

    def test_stale_stops_with_results(self):
        assert bc._should_stop_collect(10, 30, 1, 0, None)[0] is True

    def test_stale_resets_on_added(self):
        stop, stale = bc._should_stop_collect(10, 30, 2, 3, None)
        assert stop is False and stale == 0

    def test_no_results_no_stop(self):
        assert bc._should_stop_collect(0, 30, 1, 0, None)[0] is False


class TestHomeRelative:
    """config.home_relative 路径可移植：$HOME 下绝对路径 → ~ 相对形式。"""

    def test_home_prefix_to_tilde(self):
        home = os.path.expanduser("~")
        assert config.home_relative(home + "/.xhs-leads-profile") == "~/.xhs-leads-profile"

    def test_home_itself_to_tilde(self):
        assert config.home_relative(os.path.expanduser("~")) == "~"

    def test_non_home_path_unchanged(self):
        assert config.home_relative("/Applications/Google Chrome.app") == "/Applications/Google Chrome.app"
        assert config.home_relative("/x") == "/x"

    def test_empty_unchanged(self):
        assert config.home_relative("") == ""


class TestBuiltinCftCandidates:
    def test_dev_mode_no_meipass_returns_none_or_home(self, monkeypatch):
        """开发态无 _MEIPASS：候选=home 下 CFT；不存在则 None。"""
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        r = etc.builtin_cft_candidates()
        # 开发机未必装 CFT → None 或 home 路径都接受（只固化不抛异常）
        assert r is None or "chrome.for.testing" in r.lower() or "Chrome for Testing" in r


class TestSavedCookies:
    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bc.config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(bc.pool, "peek", lambda a: None)
        assert bc._saved_cookies("default") == []

    def test_reads_default_cookies_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bc.config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(bc.pool, "peek", lambda a: None)
        (tmp_path / "cookies.json").write_text(
            '{"cookies": [{"name": "web_session", "value": "x"}]}', encoding="utf8")
        out = bc._saved_cookies("default")
        assert out == [{"name": "web_session", "value": "x"}]


class TestHandleLoginWall:
    def test_calls_mark_manual(self, monkeypatch):
        called = {}
        def fake_mark_manual(account_id, reason=""):
            called["a"] = account_id; called["r"] = reason
        monkeypatch.setattr(bc.risk, "mark_manual", fake_mark_manual)
        bc._handle_login_wall("acc1", "登录墙")
        assert called == {"a": "acc1", "r": "登录墙"}



# ------------------------------------------------ fake engine 委托路径

import asyncio as _asyncio
from tests.fake_playwright import FakeEngine, FakeContext, FakePage  # noqa: F401


def test_should_stop_collect_logic():
    # 达到 limit → stop
    stopped, n = bc._should_stop_collect(30, 30, 0, 0, None)
    assert stopped is True
    # 未达 limit 且无 stale → 继续
    stopped, n = bc._should_stop_collect(5, 30, 0, 0, None)
    assert stopped is False
    # stale 高 → stop
    stopped, n = bc._should_stop_collect(5, 30, 20, 0, None)
    assert stopped is True


class _UrlPage:
    """只有 url 的假页面：URL 判定分支不需要 CDP。"""

    def __init__(self, url: str) -> None:
        self.url = url


class TestDetectVerifyWallUrl:
    """R-6.53z6：平台人机验证页必须被识别（原判定只看 /login 与中文文案，
    实测 captcha 页两者都不命中 → 0 条被误报成「限流」）。"""

    def _detect(self, url):
        return asyncio.run(bc._detect_login_wall(_UrlPage(url)))

    def test_captcha_page_is_wall(self):
        assert "人机验证" in self._detect(
            "https://www.xiaohongshu.com/website-login/captcha?verifyBiz=461")

    def test_website_login_page_is_wall(self):
        assert "人机验证" in self._detect("https://www.xiaohongshu.com/website-login")

    def test_login_page_reports_session(self):
        assert "登录" in self._detect("https://www.xiaohongshu.com/login")

    def test_normal_search_page_not_wall(self):
        assert self._detect("https://www.xiaohongshu.com/search_result?keyword=x") == ""
