"""login_session 事件驱动登录会话测试。

覆盖：
- is_real_login_me 纯函数判定矩阵（guest 严格为 false）
- LoginSession 事件流程：user/me guest=false → 确认 id_token → 切无头
- 超时退出、引擎未运行、id_token 缺失仅告警
- 模块级 start/stop 注册表与幂等
"""
import asyncio

import pytest

from src import engine_pool, login_session


class TestIsRealLoginMe:
    @pytest.mark.parametrize("payload,expected", [
        ({"data": {"guest": False, "nickname": "x"}}, True),   # 真实登录
        ({"data": {"guest": True}}, False),                    # 游客
        ({"data": {}}, False),                                 # guest 缺失
        ({"data": None}, False),                               # 无 data
        ({}, False),                                           # 空响应
        (None, False),                                         # 异常
    ])
    def test_matrix(self, payload, expected):
        assert login_session.is_real_login_me(payload) is expected


class _FakeCtx:
    def __init__(self):
        self.listeners = {}
        self.removed = []

    def on(self, event, cb):
        self.listeners.setdefault(event, []).append(cb)

    def remove_listener(self, event, cb):
        self.removed.append(event)
        self.listeners.get(event, []).remove(cb)

    async def emit(self, resp):
        for cb in list(self.listeners.get("response", [])):
            cb(resp)


class _FakeResp:
    def __init__(self, url, payload):
        self.url = url
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeEng:
    def __init__(self, cookies):
        self._ctx = _FakeCtx()
        self._cookies = cookies

    async def context(self):
        return self._ctx

    async def read_cookies(self):
        return self._cookies


def _patch_common(monkeypatch, eng):
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _a: eng)
    monkeypatch.setattr(login_session, "LOGIN_STABLE", 0)
    switched = {}

    async def _set_window(aid, on):
        switched["v"] = (aid, on)
        return {"ok": True}

    return switched, _set_window


class TestLoginSessionRun:
    def test_switches_headless_after_real_login_event(self, monkeypatch):
        eng = _FakeEng([{"name": "id_token", "value": "x"}])
        switched, _set_window = _patch_common(monkeypatch, eng)
        from src import engine_window  # noqa: PLC0415
        monkeypatch.setattr(engine_window, "set_window", _set_window)

        async def scenario():
            sess = login_session.LoginSession("acc_x")
            run_task = asyncio.create_task(sess._run())
            await asyncio.sleep(0.05)  # 等监听挂上
            # 先一次游客响应（不应触发）
            await eng._ctx.emit(_FakeResp(
                login_session.USER_ME_MARK, {"data": {"guest": True}}))
            await asyncio.sleep(0.05)
            # 真实登录响应
            await eng._ctx.emit(_FakeResp(
                login_session.USER_ME_MARK,
                {"data": {"guest": False, "nickname": "x"}}))
            await run_task
            return sess

        sess = asyncio.run(scenario())
        assert switched["v"] == ("acc_x", False)
        # 监听已清理
        assert "response" in sess._ctx.removed

    def test_ignores_non_user_me_url(self, monkeypatch):
        eng = _FakeEng([{"name": "id_token", "value": "x"}])
        switched, _set_window = _patch_common(monkeypatch, eng)
        from src import engine_window  # noqa: PLC0415
        monkeypatch.setattr(engine_window, "set_window", _set_window)
        monkeypatch.setattr(login_session, "LOGIN_TIMEOUT", 0.2)

        async def scenario():
            sess = login_session.LoginSession("acc_x")
            t = asyncio.create_task(sess._run())
            await asyncio.sleep(0.05)
            await eng._ctx.emit(_FakeResp(
                "https://x/other/api", {"data": {"guest": False}}))
            await t  # 应超时退出，不切无头

        asyncio.run(scenario())
        assert "v" not in switched

    def test_timeout_when_no_login(self, monkeypatch):
        eng = _FakeEng([])
        switched, _set_window = _patch_common(monkeypatch, eng)
        from src import engine_window  # noqa: PLC0415
        monkeypatch.setattr(engine_window, "set_window", _set_window)
        monkeypatch.setattr(login_session, "LOGIN_TIMEOUT", 0.1)

        async def scenario():
            sess = login_session.LoginSession("acc_x")
            await sess._run()

        asyncio.run(scenario())
        assert "v" not in switched

    def test_engine_missing_returns(self, monkeypatch):
        monkeypatch.setattr(engine_pool.pool, "peek", lambda _a: None)

        async def scenario():
            await login_session.LoginSession("acc_x")._run()

        asyncio.run(scenario())  # 不抛、不切

    def test_missing_id_token_only_warns_but_switches(self, monkeypatch):
        eng = _FakeEng([{"name": "web_session", "value": "y"}])  # 无 id_token
        switched, _set_window = _patch_common(monkeypatch, eng)
        from src import engine_window  # noqa: PLC0415
        monkeypatch.setattr(engine_window, "set_window", _set_window)

        async def scenario():
            sess = login_session.LoginSession("acc_x")
            t = asyncio.create_task(sess._run())
            await asyncio.sleep(0.05)
            await eng._ctx.emit(_FakeResp(
                login_session.USER_ME_MARK, {"data": {"guest": False}}))
            await t

        asyncio.run(scenario())
        assert switched["v"] == ("acc_x", False)


class TestSessionRegistry:
    def test_start_and_stop(self, monkeypatch):
        login_session._SESSIONS.clear()

        async def scenario():
            sess = login_session.start_login_session("acc_y")
            assert sess is not None
            assert "acc_y" in login_session._SESSIONS
            # 重复开启返回 None（幂等）
            assert login_session.start_login_session("acc_y") is None
            stopped = await login_session.stop_login_session("acc_y")
            assert stopped is True
            assert "acc_y" not in login_session._SESSIONS

        asyncio.run(scenario())

    def test_stop_missing_returns_false(self):
        assert asyncio.run(login_session.stop_login_session("nope")) is False
