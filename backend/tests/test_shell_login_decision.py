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

class TestForcedMonitorNoPullBack:
    """R-6.72（用户拍板：守护会造成不可控局面，完全抛弃自动干预）。

    离开登录页（含 chrome-error://chromewebdata/ 加载失败死页）= 未登录，
    直接判定结束监视并交回上层；**绝不 load_url 拉回**。旧逻辑「一离开 /login
    就拉回」会在死页上自激（拉回→导航事件→再判离开→再拉回），实测打满
    CPU 并触发 CFT Chrome_IOThread CHECK 崩溃。
    """

    def _run(self, cookies, url):
        import asyncio
        from src.shell_window import _guard_monitor_forced

        class _W:
            async def current_url(self):
                return url

            async def load_url(self, u, **k):
                raise AssertionError("R-6.72 禁止任何自动拉回/导航")

            async def evaluate_js(self, js):
                return None

        return asyncio.run(_guard_monitor_forced(_W(), "b", cookies, {"import": 0.0}))

    def test_leave_login_page_returns_false_without_navigation(self):
        # 未登录 + 已离开登录页（死页）→ False 结束监视，且一次导航都不做
        assert self._run(
            [{"name": "a", "value": "1"}], "chrome-error://chromewebdata/"
        ) is False

    def test_leave_login_page_explore_returns_false(self):
        assert self._run(
            [{"name": "a", "value": "1"}], "https://www.xiaohongshu.com/explore"
        ) is False

    def test_still_on_login_page_keeps_monitoring(self):
        # 仍在登录页 → None（继续等事件），不强制结束
        assert self._run(
            [{"name": "a", "value": "1"}], "https://www.xiaohongshu.com/login"
        ) is None
