"""引擎窗口开关（set_window）的守卫行为。

原「事件转发」相关测试随能力下线一并删除（R-6.53z8p）。
"""
import asyncio

from src import config, engine_pool, engine_window, login_session, server  # noqa: F401


def test_set_window_refuses_while_collecting():
    """采集进行中切换窗口会重启引擎，必须先拒绝。"""
    server._COLLECT_TASKS["t1"] = {"status": "running", "account_id": "default"}
    try:
        r = asyncio.run(engine_window.set_window("default", on=True))
    finally:
        server._COLLECT_TASKS.pop("t1", None)
    assert r["ok"] is False
    assert "采集任务进行中" in r["error"]


def test_set_window_noop_when_already_target_mode(monkeypatch):
    """已经是目标模式时不重启引擎（重启代价高且会断登录态）。"""
    monkeypatch.setattr(config, "get_config", lambda: {"headless": False})
    r = asyncio.run(engine_window.set_window("default", on=True))
    assert r["ok"] is True
    assert r["note"] == "已是目标模式，无需切换"


def test_set_window_noop_for_headless_target(monkeypatch):
    """反向：要回无头但本来就是无头（default 已禁止切无头，此处用普通账号）。"""
    monkeypatch.setattr(config, "account_headless", lambda _aid: True)
    r = asyncio.run(engine_window.set_window("acc_x", on=False))
    assert r["ok"] is True
    assert r["note"] == "已是目标模式，无需切换"


def test_set_window_refuses_default_to_headless():
    """R-6.58：default 承载 APP 壳窗与采集窗，运行中切无头会把整个应用带走。"""
    r = asyncio.run(engine_window.set_window("default", on=False))
    assert r["ok"] is False
    assert "default 账号" in r["error"]


# ------------------------------------------------ R-6.60 close_account

def test_close_account_refuses_default():
    """default 不能单独关：关掉等于关掉整个应用。"""
    r = asyncio.run(engine_window.close_account("default"))
    assert r["ok"] is False
    assert "default 账号" in r["error"]


def test_close_account_rejects_running_task():
    """有采集任务在跑时关引擎会打断任务，必须先拒绝。"""
    server._COLLECT_TASKS["t9"] = {"status": "running", "account_id": "acc_x"}
    try:
        r = asyncio.run(engine_window.close_account("acc_x"))
    finally:
        server._COLLECT_TASKS.pop("t9", None)
    assert r["ok"] is False
    assert "采集任务进行中" in r["error"]


def test_close_account_noop_when_engine_not_running(monkeypatch):
    """没开过的账号也允许「关」，幂等返回，不报错。"""
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: None)
    r = asyncio.run(engine_window.close_account("acc_x"))
    assert r["ok"] is True
    assert "未运行" in r["note"]


def test_close_account_closes_running_engine(monkeypatch):
    """引擎在跑：真的调 pool.close，并回报已释放。"""
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: object())

    async def _close(_aid):
        return True

    monkeypatch.setattr(engine_pool.pool, "close", _close)
    r = asyncio.run(engine_window.close_account("acc_x"))
    assert r["ok"] is True
    assert "已关闭" in r["note"]


# ------------------------------------------------ R-6.62 最小化/恢复

class _FakeVisibleEng:
    """有头引擎 stub：记录最小化/恢复调用次数。"""

    def __init__(self):
        self.min_n = 0
        self.rest_n = 0

    async def minimize_window(self):
        self.min_n += 1
        return True

    async def restore_window(self):
        self.rest_n += 1
        return True


def test_minimize_refuses_default():
    r = asyncio.run(engine_window.minimize_account("default"))
    assert r["ok"] is False


def test_minimize_noop_when_engine_not_running(monkeypatch):
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: None)
    r = asyncio.run(engine_window.minimize_account("acc_x"))
    assert r["ok"] is True
    assert "未运行" in r["note"]


def test_minimize_skips_headless_account(monkeypatch):
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: object())
    monkeypatch.setattr(config, "account_headless", lambda _aid: True)
    r = asyncio.run(engine_window.minimize_account("acc_x"))
    assert r["ok"] is True
    assert "无头" in r["note"]


def test_minimize_calls_engine(monkeypatch):
    fake = _FakeVisibleEng()
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: fake)
    monkeypatch.setattr(config, "account_headless", lambda _aid: False)
    r = asyncio.run(engine_window.minimize_account("acc_x"))
    assert r["ok"] is True
    assert fake.min_n == 1


def test_restore_calls_engine(monkeypatch):
    fake = _FakeVisibleEng()
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: fake)
    monkeypatch.setattr(config, "account_headless", lambda _aid: False)
    r = asyncio.run(engine_window.restore_account("acc_x"))
    assert r["ok"] is True
    assert fake.rest_n == 1


def test_minimize_all_only_running_visible(monkeypatch):
    """全部收起：只动在跑且有头的账号，default 与没开的都跳过。"""
    fake = _FakeVisibleEng()
    monkeypatch.setattr(config, "list_accounts",
                        lambda: {"default": {}, "acc_run": {}, "acc_off": {}})

    def _peek(aid):
        return fake if aid == "acc_run" else None

    monkeypatch.setattr(engine_pool.pool, "peek", _peek)
    monkeypatch.setattr(config, "account_headless", lambda _aid: False)
    r = asyncio.run(engine_window.minimize_all_accounts())
    assert r["ok"] is True
    assert r["minimized"] == ["acc_run"]
    assert fake.min_n == 1


# ------------------------------------------------ R-6.62 可见窗口 LRU 上限

def test_mark_visible_lru_order():
    ep = engine_pool.EnginePool()
    ep.mark_visible("a")
    ep.mark_visible("b")
    ep.mark_visible("a")  # 重复可见 → 提到末尾
    assert ep.visible_order() == ["b", "a"]
    ep.mark_hidden("b")
    assert ep.visible_order() == ["a"]


def test_enforce_visible_cap_hides_oldest():
    """已有 3 个可见，开第 4 个时把最旧的最小化。"""
    ep = engine_pool.EnginePool()
    fake = _FakeVisibleEng()
    for aid in ("a", "b", "c"):
        ep._engines[aid] = fake
        ep.mark_visible(aid)
    hidden = asyncio.run(ep.enforce_visible_cap("d", limit=3))
    assert hidden == ["a"]
    assert ep.visible_order() == ["b", "c"]
    assert fake.min_n == 1


def test_enforce_visible_cap_noop_under_limit():
    ep = engine_pool.EnginePool()
    ep.mark_visible("a")
    hidden = asyncio.run(ep.enforce_visible_cap("b", limit=3))
    assert hidden == []


def test_restore_reports_made_room(monkeypatch):
    """恢复时可见已满：先 enforce 收起最旧，返回 made_room。"""
    fake = _FakeVisibleEng()
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: fake)
    monkeypatch.setattr(config, "account_headless", lambda _aid: False)

    async def _enforce(_aid, limit=None):
        return ["old1"]

    monkeypatch.setattr(engine_pool.pool, "enforce_visible_cap", _enforce)
    r = asyncio.run(engine_window.restore_account("acc_x"))
    assert r["ok"] is True
    assert r["made_room"] == ["old1"]
    assert fake.rest_n == 1


# ------------------------------------------------ R-6.67 登录完成自动切无头（事件驱动）

def test_start_auto_refuses_default():
    r = asyncio.run(engine_window.start_auto_headless("default"))
    assert r["ok"] is False


def test_start_auto_rejects_unknown(monkeypatch):
    monkeypatch.setattr(config, "get_account", lambda _a: None)
    r = asyncio.run(engine_window.start_auto_headless("nope"))
    assert r["ok"] is False


def test_start_auto_noop_when_already_headless(monkeypatch):
    monkeypatch.setattr(config, "get_account", lambda _a: {"account_id": "acc_x"})
    monkeypatch.setattr(config, "account_headless", lambda _a: True)
    r = asyncio.run(engine_window.start_auto_headless("acc_x"))
    assert r["ok"] is True and "已是无头" in r["note"]


def test_start_auto_starts_event_session(monkeypatch):
    """正常开启：委托 login_session 启动事件监听。"""
    monkeypatch.setattr(config, "get_account", lambda _a: {"account_id": "acc_x"})
    monkeypatch.setattr(config, "account_headless", lambda _a: False)
    started = {}

    def _start(aid):
        started["aid"] = aid
        return object()  # 非 None = 新会话已建

    monkeypatch.setattr(login_session, "start_login_session", _start)
    r = asyncio.run(engine_window.start_auto_headless("acc_x"))
    assert r["ok"] is True
    assert started["aid"] == "acc_x"
    assert "事件监听" in r["note"]


def test_start_auto_idempotent_when_session_exists(monkeypatch):
    monkeypatch.setattr(config, "get_account", lambda _a: {"account_id": "acc_x"})
    monkeypatch.setattr(config, "account_headless", lambda _a: False)
    monkeypatch.setattr(login_session, "start_login_session", lambda _a: None)
    r = asyncio.run(engine_window.start_auto_headless("acc_x"))
    assert r["ok"] is True and "无需重复开启" in r["note"]


def test_cancel_auto_stops_session(monkeypatch):
    flags = {"acc_x": True}
    monkeypatch.setattr(config, "set_account_auto_headless",
                        lambda a, on: flags.__setitem__(a, on))

    async def _stop(aid):
        flags["stopped"] = aid
        return True

    monkeypatch.setattr(login_session, "stop_login_session", _stop)
    r = asyncio.run(engine_window.cancel_auto_headless("acc_x"))
    assert r["ok"] is True
    assert flags["acc_x"] is False and flags["stopped"] == "acc_x"


# ------------------------------------------------ R-6.66 登录走 explore 浏览页

def test_login_page_navigates_explore_when_no_page(monkeypatch):
    """无现成小红书页：新建并导航 explore（未登录自动弹登录 modal），不进 /login。"""
    gotos = []

    class _Page:
        def is_closed(self):
            return False

    class _Ctx:
        def __init__(self):
            self.pages = []  # 无现成页

    class _Eng:
        async def context(self):
            return _Ctx()

        async def new_page(self):
            class _P:
                async def goto(self, url, wait_until=None, timeout=None):
                    gotos.append(url)
            return _P()

    async def _get(_a):
        return _Eng()

    monkeypatch.setattr(engine_pool.pool, "get", _get)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(engine_window.asyncio, "sleep", _no_sleep)
    asyncio.run(engine_window.login_page("acc_x"))
    assert gotos == [engine_window.XHS_EXPLORE]
    assert engine_window.XHS_LOGIN not in gotos


def test_login_page_reuses_existing_xhs_page(monkeypatch):
    """已有小红书页时直接复用，不新建、不导航。"""
    class _P:
        def is_closed(self):
            return False

        url = "https://www.xiaohongshu.com/explore"

    class _Ctx:
        def __init__(self):
            self.pages = [_P()]

    class _Eng:
        def __init__(self):
            self.new_called = False

        async def context(self):
            return _Ctx()

        async def new_page(self):
            self.new_called = True
            raise AssertionError("不应新建页面")

    eng = _Eng()

    async def _get(_a):
        return eng

    monkeypatch.setattr(engine_pool.pool, "get", _get)
    pg = asyncio.run(engine_window.login_page("acc_x"))
    assert isinstance(pg, _P)
    assert eng.new_called is False
