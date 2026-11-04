"""R-6.48n：守卫登录态判定纯函数测试（has_id_token 统一 3 处内联）。"""
import pytest

from src.shell_window import has_id_token


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
