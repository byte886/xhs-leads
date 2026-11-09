"""login_session 登录判定与身份缓存测试。

覆盖：
- is_real_login_me 纯函数判定矩阵（guest 严格为 false）
- capture_user_me 抓取与超时/引擎未运行/缺页分支
- login_identity 三标识与 cache_identity 落盘
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


class TestLoginNickname:
    """R-6.69：登录事件顺带取昵称——账号列表靠它认出「这个号是谁」。"""

    @pytest.mark.parametrize("payload,expected", [
        ({"data": {"guest": False, "nickname": "小红薯"}}, "小红薯"),  # 已登录样例昵称
        ({"data": {"guest": True, "user_id": "x"}}, ""),              # 游客无昵称
        ({"data": {"nickname": "  珠宝号  "}}, "珠宝号"),             # 去首尾空白
        ({"data": {"nickname": ""}}, ""),                             # 空昵称
        ({"data": {"nickname": 123}}, ""),                            # 非字符串不污染注册表
        ({"data": None}, ""),                                         # 无 data
        ({}, ""),                                                     # 空响应
        (None, ""),                                                   # 异常
    ])
    def test_matrix(self, payload, expected):
        assert login_session.login_nickname(payload) == expected


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


class _FakePage:
    """模拟页面自身触发的 user/me 请求（真实链路靠页面请求，不是裸 fetch）。"""

    def __init__(self, ctx, payload, url="https://edith.xiaohongshu.com/api/sns/web/v2/user/me"):
        self._ctx = ctx
        self._payload = payload
        self._url = url
        self.reloaded = 0

    async def reload(self, **_kw):
        self.reloaded += 1
        await self._ctx.emit(_FakeResp(self._url, self._payload))


class TestCaptureUserMe:
    """补昵称读取（R-6.69）：历史账号在昵称缓存上线前就登录过，需一次手动补。"""

    def test_no_engine_returns_none(self, monkeypatch):
        monkeypatch.setattr(engine_pool.pool, "peek", lambda _a: None)
        assert asyncio.run(login_session.capture_user_me("acc_z")) is None

    def test_reads_first_user_me(self, monkeypatch):
        from src import engine_window

        eng = _FakeEng([])
        ctx = eng._ctx  # 页面必须挂在**同一个** context 上，监听才收得到
        monkeypatch.setattr(engine_pool.pool, "peek", lambda _a: eng)
        page = _FakePage(ctx, {"data": {"guest": False, "nickname": "小红薯"}})

        async def _login_page(_aid):
            return page

        monkeypatch.setattr(engine_window, "login_page", _login_page)

        payload = asyncio.run(login_session.capture_user_me("acc_z", timeout=5))
        assert payload is not None
        assert payload["data"]["nickname"] == "小红薯"
        assert page.reloaded == 1
        # 监听必须被摘掉，否则账号常驻时会不断累积
        assert ctx.removed == ["response"]

    def test_unrelated_response_ignored(self, monkeypatch):
        """无关 URL 不settle：超时按取不到处理。"""
        from src import engine_window

        eng = _FakeEng([])
        ctx = eng._ctx
        monkeypatch.setattr(engine_pool.pool, "peek", lambda _a: eng)
        page = _FakePage(ctx, {"data": {"guest": False}},
                         url="https://edith.xiaohongshu.com/api/sns/web/v1/config")

        async def _login_page(_aid):
            return page

        monkeypatch.setattr(engine_window, "login_page", _login_page)
        assert asyncio.run(login_session.capture_user_me("acc_z", timeout=0.2)) is None
        assert ctx.removed == ["response"]



class TestLoginIdentity:
    """user/me → 三种标识（昵称 / 小红书号 / 内部 uid），登录那刻一次性落盘。"""

    @pytest.mark.parametrize("payload,nick,red,uid", [
        ({"data": {"guest": False, "nickname": "小红薯", "red_id": "123456789",
                   "user_id": "abcdef0000000000000001"}},
         "小红薯", "123456789", "abcdef0000000000000001"),
        ({"data": {"guest": True}}, "", "", ""),               # 游客：三项全空
        ({"data": {}}, "", "", ""),
        ({}, "", "", ""),
        ({"data": {"nickname": 123, "red_id": None, "user_id": ["x"]}},
         "", "", ""),                                          # 非字符串一律当空
        ({"data": {"nickname": "  小红薯  ", "red_id": " 123456789 ",
                   "user_id": " u1 "}},
         "小红薯", "123456789", "u1"),                          # 两侧空白剥掉
    ])
    def test_matrix(self, payload, nick, red, uid):
        from src import login_session

        ident = login_session.login_identity(payload)
        assert ident == {"nickname": nick, "red_id": red, "uid": uid}

    def test_cache_writes_all_three(self, monkeypatch, tmp_path):
        from src import config, login_session

        monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        config.add_account("acc_a")
        ident = login_session.cache_identity("acc_a", {"data": {
            "guest": False, "nickname": "小红薯", "red_id": "123456789",
            "user_id": "abcdef0000000000000001"}})
        assert ident["red_id"] == "123456789"
        assert config.account_nickname("acc_a") == "小红薯"
        assert config.account_red_id("acc_a") == "123456789"
        assert config.account_platform_uid("acc_a") == "abcdef0000000000000001"
        assert config.account_display_name("acc_a") == "小红薯"

    def test_cache_does_not_blank_existing_values(self, monkeypatch, tmp_path):
        """平台偶发只回昵称不回 red_id：已有的小红书号不能被空值冲掉。"""
        from src import config, login_session

        monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        config.add_account("acc_a")
        config.set_account_red_id("acc_a", "123456789")
        login_session.cache_identity("acc_a", {"data": {
            "guest": False, "nickname": "新昵称"}})
        assert config.account_red_id("acc_a") == "123456789"
        assert config.account_nickname("acc_a") == "新昵称"
