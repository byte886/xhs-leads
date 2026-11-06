"""守卫登录态判定纯函数测试（R-6.48n has_id_token；R-6.53z7 has_login_cookie）。"""
import pytest

from src.shell_window import has_id_token, has_login_cookie


class TestHasIdToken:
    """cookies 登录态判定矩阵（纯函数，不碰浏览器）。"""

    @pytest.mark.parametrize(
        "cookies,expected",
        [
            (None, False),                       # 无 cookies
            ([], False),                         # 空列表
            ([{"name": "web_session", "value": "x"}], False),   # 未登录（无 id_token）
            ([{"name": "id_token", "value": "abc"}], True),     # 已登录
            (
                [{"name": "a", "value": "1"}, {"name": "id_token", "value": "t"}],
                True,  # 混合 cookies 含 id_token
            ),
            (
                [{"name": "id_token", "value": ""}],
                True,  # id_token 存在即视为登录（值空也计数——语义保持原内联行为）
            ),
        ],
    )
    def test_matrix(self, cookies, expected):
        assert has_id_token(cookies) is expected

    def test_not_boolable_input(self):
        # 非列表/不可迭代输入不炸（防御：调用方可能传 None/异常结构）
        assert has_id_token(123) is False


class TestHasLoginCookie:
    """R-6.53z7：id_token 或 web_session 任一存在即已登录。

    只认 id_token 会导致「只有 web_session 的会话被判未登录」，守卫随即把已跳到
    explore 的页面拉回登录页——用户实测到的「登录成功又被弹回登录页」。
    """

    @pytest.mark.parametrize(
        "cookies,expected",
        [
            (None, False),
            ([], False),
            ([{"name": "web_session", "value": "x"}], True),      # 只有 web_session 也算登录
            ([{"name": "id_token", "value": "abc"}], True),
            ([{"name": "a", "value": "1"}], False),                # 无关 cookie
            ([{"name": "web_session", "value": ""}], True),        # 存在即算（值空不判空）
        ],
    )
    def test_matrix(self, cookies, expected):
        assert has_login_cookie(cookies) is expected

    def test_not_iterable(self):
        assert has_login_cookie(123) is False


class _GuardWin:
    """假壳窗：只实现守卫用到的 cookies / load_url。"""

    def __init__(self, cookies):
        self._cookies = cookies
        self.loaded: list[str] = []

    async def cookies(self):
        return self._cookies

    async def load_url(self, url):
        self.loaded.append(url)


class TestGuardReleaseWhenLoggedIn:
    """R-6.53z7 用户口径：登录成功后页面自己跳 explore，守卫必须放行、不许拉回。"""

    def _run(self, cookies):
        import asyncio
        import threading

        from src.shell_window import _guard_pull_back_if_logged_out

        win = _GuardWin(cookies)
        stop = threading.Event()
        asyncio.run(_guard_pull_back_if_logged_out(
            win, stop, "https://www.xiaohongshu.com/explore"))
        return win.loaded

    def test_logged_in_is_released(self):
        """已登录（web_session）→ 一次导航都不做，让页面停在 explore。"""
        assert self._run([{"name": "web_session", "value": "x"}]) == []

    def test_logged_in_by_id_token_is_released(self):
        assert self._run([{"name": "id_token", "value": "t"}]) == []

    def test_logged_out_is_pulled_back(self):
        """未登录乱点离开登录页 → 拉回登录页（防白屏）。"""
        from src.shell_window import XHS_LOGIN_URL
        assert self._run([{"name": "a", "value": "1"}]) == [XHS_LOGIN_URL]
