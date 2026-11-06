"""URL 构造与参数检查（R-6.53z6）。

历史缺口：URL 用裸字符串拼接，参数为空/None 时会把 `None`/`null` 拼进 query；
登录页还硬编码了实测无作用的 `redirectPath` 后缀。
"""
import pytest

from src import shell_window
from src.urls import build_url


class TestBuildUrl:
    def test_no_params(self):
        assert build_url("https://www.xiaohongshu.com", "/explore") == \
            "https://www.xiaohongshu.com/explore"

    def test_base_only(self):
        assert build_url("https://www.xiaohongshu.com/") == "https://www.xiaohongshu.com"

    def test_dirty_values_are_dropped(self):
        """None / 空串 / null / undefined 一律不拼——不许出现 ?x=null 这类脏后缀。"""
        for dirty in (None, "", "  ", "null", "NULL", "none", "undefined", "NaN"):
            url = build_url("https://a.com", "/s", {"k": dirty})
            assert url == "https://a.com/s", (dirty, url)

    def test_dirty_value_does_not_kill_good_one(self):
        url = build_url("https://a.com", "/s", {"keyword": "翡翠", "xsec_token": None})
        assert url == "https://a.com/s?keyword=%E7%BF%A1%E7%BF%A0"

    def test_values_are_quoted(self):
        url = build_url("https://a.com", "/s", {"keyword": "翡翠 手串&x"})
        assert url == "https://a.com/s?keyword=%E7%BF%A1%E7%BF%A0%20%E6%89%8B%E4%B8%B2%26x"

    def test_illegal_chars_rejected(self):
        for bad in ("a\nb", 'a"b', "a<b", "a\\b"):
            with pytest.raises(ValueError):
                build_url("https://a.com", "/s", {"k": bad})

    def test_slash_normalized(self):
        assert build_url("https://a.com/", "/s/") == "https://a.com/s/"


class TestLoginUrlIsClean:
    def test_no_query_suffix(self):
        """登录页不许带参数（实测 redirectPath 对跳转无作用，只会脏地址栏）。"""
        assert "?" not in shell_window.XHS_LOGIN_URL
        assert shell_window.XHS_LOGIN_URL == "https://www.xiaohongshu.com/login"
