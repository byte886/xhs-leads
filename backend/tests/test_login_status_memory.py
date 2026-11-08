"""登录态「失效记忆」行为测试（R-6.72）。

用户口径：未登录就直接返回未登录，不要等来等去。失效记忆不能变成
「已登录却永远被判未登录、只能重启 app」的隐性墙。

现场证据（2026-11-08 21:14 `/api/login_status`）：
    raw = "web_session=有 页面登录=是 被踢=否 风控需验证=否 失效记忆=是"
    → logged_in 却是 false。即引擎登录态完全有效，被陈旧记忆一票否决。
"""
from src import server


class TestLoginStatusMemory:
    def test_valid_login_clears_stale_memory(self, monkeypatch):
        """实测已登录 → 立即解除失效记忆（本问题回归锁）。"""
        monkeypatch.setattr(server, "_LOGIN_INVALID", True)
        r = server._build_login_status_result(
            has_ws=True, page_logged_in=True, kicked=False, risk_verify=False)
        assert r["logged_in"] is True
        assert r["status"] == "logged_in"
        assert server._LOGIN_INVALID is False, "实测有效必须清掉失效记忆"

    def test_kicked_sets_memory(self, monkeypatch):
        monkeypatch.setattr(server, "_LOGIN_INVALID", False)
        r = server._build_login_status_result(
            has_ws=True, page_logged_in=True, kicked=True, risk_verify=False)
        assert r["logged_in"] is False
        assert r["status"] == "kicked"
        assert server._LOGIN_INVALID is True

    def test_risk_verify_sets_memory(self, monkeypatch):
        monkeypatch.setattr(server, "_LOGIN_INVALID", False)
        r = server._build_login_status_result(
            has_ws=True, page_logged_in=True, kicked=False, risk_verify=True)
        assert r["logged_in"] is False
        assert server._LOGIN_INVALID is True

    def test_not_logged_in_keeps_memory_untouched(self, monkeypatch):
        """未登录（无 web_session）不改记忆：只有明确被踢/风控才置位。"""
        monkeypatch.setattr(server, "_LOGIN_INVALID", False)
        r = server._build_login_status_result(
            has_ws=False, page_logged_in=False, kicked=False, risk_verify=False)
        assert r["logged_in"] is False
        assert server._LOGIN_INVALID is False
