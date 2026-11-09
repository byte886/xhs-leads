"""壳窗口助手函数单测（URL 判定 / 标题守护 / 窗口校准 / Cookie 同步）。

这些函数都是「失败也必须静默」的类型：后台起窗、注入标题、摆位、同步
Cookie 都不允许把异常抛给主流程。测试逐条守住这条，避免以后有人把
「吞异常」当成冗余代码删掉，导致启动阶段被单点失败打断。
"""
import asyncio

from src import config
from src import shell_window as sw


# ---------------------------------------------------------------- 假对象


async def _fake_cdp_call(aw, timeout=None, label="", skip=False):
    """cdp_call 直通版：直接 await 传入的 coroutine（不起超时保护）。"""
    return await aw


class _Pg:
    def __init__(self, url="", fail_url=False):
        self.url = url
        self._fail_url = fail_url

    def is_closed(self):
        return False


class _PgUrlBoom(_Pg):
    """读 url 就抛异常（页面已销毁）。"""

    def __init__(self):
        # 不走父类 __init__：本类 url 是只读 property，赋值会炸
        pass

    @property
    def url(self):
        raise RuntimeError("页面已销毁")


class _PgTitle:
    """标题守护用页面：记录 add_init_script / evaluate 调用次数。"""

    def __init__(self, installed=False, fail_init=False, fail_eval=False):
        self._shell_title_installed = installed
        self._debug_title_installed = installed
        self.fail_init = fail_init
        self.fail_eval = fail_eval
        self.init_calls = 0
        self.eval_calls = 0

    async def add_init_script(self, js):
        if self.fail_init:
            raise RuntimeError("页面未就绪")
        self.init_calls += 1

    async def evaluate(self, js):
        self.eval_calls += 1
        if self.fail_eval:
            raise RuntimeError("页面已关闭")
        return "ok"


class _CdpSession:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.sent = []

    async def send(self, method, args=None):
        if method == self.fail_at:
            raise RuntimeError("CDP 假死")
        self.sent.append(method)
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}


class _PgCal:
    def __init__(self, cdp):
        self._cdp = cdp
        self.context = self

    async def new_cdp_session(self, page):
        return self._cdp


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------ URL 判定类


def test_safe_url_normal_and_boom():
    assert sw._safe_url(_Pg(url="https://www.xiaohongshu.com/explore")) \
        == "https://www.xiaohongshu.com/explore"
    assert sw._safe_url(_PgUrlBoom()) == ""  # 页已销毁 → 空串，不抛


def test_is_browsing_page_variants():
    assert asyncio.run(sw._is_browsing_page(
        _Pg(url="https://www.xiaohongshu.com/explore")) ) is True
    assert asyncio.run(sw._is_browsing_page(
        _Pg(url="https://www.xiaohongshu.com/login")) ) is False
    assert asyncio.run(sw._is_browsing_page(_PgUrlBoom())) is False


def test_page_key_normalizes_and_filters():
    p = _Pg(url="https://www.xiaohongshu.com/explore/?x=1#top")
    assert sw._page_key(p) == "https://www.xiaohongshu.com/explore/?x=1"
    assert sw._page_key(_Pg(url="https://example.com/a")) == ""
    assert sw._page_key(_Pg(url="")) == ""


# ------------------------------------------------------------ 标题守护类


def test_shell_title_installs_on_first_call(monkeypatch):
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)
    p = _PgTitle()
    asyncio.run(sw._ensure_shell_title(p))
    assert p.init_calls == 1               # 首次：注册 init script
    assert p._shell_title_installed is True


def test_shell_title_reuses_after_installed(monkeypatch):
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)
    p = _PgTitle(installed=True)
    asyncio.run(sw._ensure_shell_title(p))
    assert p.init_calls == 0               # 已装：不重复注册（防累积）
    assert p.eval_calls == 1               # 只刷新当前文档


def test_shell_title_silent_when_page_not_ready(monkeypatch):
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)
    p = _PgTitle(fail_init=True)
    asyncio.run(sw._ensure_shell_title(p))  # 不抛
    assert p._shell_title_installed is False  # 未标记，下次会重试


def test_debug_title_installs_and_survives_failure(monkeypatch):
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)
    ok = _PgTitle()
    asyncio.run(sw._ensure_debug_title(ok))
    assert ok.init_calls == 1 and ok._debug_title_installed is True

    bad = _PgTitle(fail_eval=True, installed=True)
    asyncio.run(sw._ensure_debug_title(bad))  # 刷新失败也静默


# ------------------------------------------------------------ 窗口校准类


def test_calibrate_window_sets_bounds():
    cdp = _CdpSession()
    asyncio.run(sw._calibrate_window(_PgCal(cdp), 960, 0, 960, 800))
    assert "Browser.getWindowForTarget" in cdp.sent
    assert "Browser.setWindowBounds" in cdp.sent


def test_calibrate_window_gives_up_silently(monkeypatch):
    """CDP 连坏 3 次也不阻断启动（只告警）。"""
    async def _noop(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)
    cdp = _CdpSession(fail_at="Browser.getWindowForTarget")
    asyncio.run(sw._calibrate_window(_PgCal(cdp), 0, 0, 100, 100))  # 不抛
    assert cdp.sent == []


# ------------------------------------------------------------ Cookie 同步


def test_post_import_cookies_hits_endpoint(monkeypatch):
    monkeypatch.setattr(config, "front_port", lambda: 8791)
    seen = {}

    def fake(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = req.data
        return _Resp(b"{}")

    monkeypatch.setattr(sw.urllib.request, "urlopen", fake)
    asyncio.run(sw._post_import_cookies([{"name": "web_session"}], fresh_scan=True))
    assert "/api/auth/import_cookies" in seen["url"]
    assert b"web_session" in seen["body"]


def test_post_import_cookies_swallows_backend_error(monkeypatch):
    """后端未就绪时同步失败不能打断 keeper。"""
    monkeypatch.setattr(config, "front_port", lambda: 8791)

    def boom(req, timeout):
        raise RuntimeError("后端未就绪")

    monkeypatch.setattr(sw.urllib.request, "urlopen", boom)
    asyncio.run(sw._post_import_cookies([], fresh_scan=False))  # 不抛


def test_goto_base_retries_then_succeeds(monkeypatch):
    """壳 tab 导航：前几次失败后成功即返回（uvicorn 起得慢的常态）。"""
    async def _noop(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)
    base = "http://127.0.0.1:8791/"

    class _PgNav:
        def __init__(self):
            self.url = "about:blank"
            self.calls = 0

        async def goto(self, url, wait_until=None):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("后端未就绪")
            self.url = url

    p = _PgNav()
    asyncio.run(sw._goto_base_with_retry(p, base))
    assert p.calls == 3
    assert p.url.startswith(base)


# ------------------------------------------------------------ 登录态判定


def test_has_id_token():
    assert sw.has_id_token([{"name": "id_token"}]) is True
    assert sw.has_id_token([{"name": "web_session"}]) is False
    assert sw.has_id_token(None) is False
    assert sw.has_id_token([]) is False


def test_has_login_cookie():
    """只认 id_token 会漏判「只有 web_session」的会话（历史：登录成功又被弹回登录页）。"""
    assert sw.has_login_cookie([{"name": "web_session"}]) is True
    assert sw.has_login_cookie([{"name": "id_token"}]) is True
    assert sw.has_login_cookie([{"name": "a1"}]) is False
    assert sw.has_login_cookie(None) is False
    assert sw.has_login_cookie([object()]) is False  # 非 dict 元素按未登录


# ------------------------------------------------------------ guard 助手


def test_guard_get_json_ok_and_none(monkeypatch):
    monkeypatch.setattr(sw, "_json_body", lambda url, timeout: {"status": "ok"})
    assert asyncio.run(sw._guard_get_json("http://b", "/x")) == {"status": "ok"}

    def boom(url, timeout):
        raise RuntimeError("后端未就绪")

    monkeypatch.setattr(sw, "_json_body", boom)
    assert asyncio.run(sw._guard_get_json("http://b", "/x")) is None


def test_guard_post_json_ok_and_error(monkeypatch):
    monkeypatch.setattr(sw, "_json_post", lambda url, payload, timeout: {"ok": True})
    assert asyncio.run(sw._guard_post_json("http://b", "/x", {})) == {"ok": True}

    def boom(url, payload, timeout):
        raise RuntimeError("后端 500")

    monkeypatch.setattr(sw, "_json_post", boom)
    res = asyncio.run(sw._guard_post_json("http://b", "/x", {"a": 1}))
    assert res["ok"] is False
    assert "后端 500" in res["error"]


def test_guard_safe_remove():
    sw._guard_safe_remove(None)  # 无钩子

    called = []
    sw._guard_safe_remove(lambda: called.append(1))
    assert called == [1]

    def boom():
        raise RuntimeError("loop 关闭中")

    sw._guard_safe_remove(boom)  # 静默，不把收尾变成崩溃


def test_guard_read_cookies():
    class _Win:
        def __init__(self, cs=None, boom=False):
            self._cs = cs
            self._boom = boom

        async def cookies(self):
            if self._boom:
                raise RuntimeError("页面没了")
            return self._cs

    assert asyncio.run(sw._guard_read_cookies(_Win([{"name": "x"}]))) == [{"name": "x"}]
    assert asyncio.run(sw._guard_read_cookies(_Win(boom=True))) == []


def test_guard_attach_page_events():
    class _PgEvents:
        def __init__(self):
            self.events = []

        def on(self, evt, cb):
            self.events.append(evt)

    ev = asyncio.Event()
    ev.set()
    p = _PgEvents()
    sw._guard_attach_page_events(p, ev, lambda: None)
    assert ev.is_set() is False  # 进入时先清标志，事件到达才唤醒
    assert p.events == ["framenavigated", "close"]


def test_guard_attach_page_events_without_source():
    ev = asyncio.Event()
    sw._guard_attach_page_events(None, ev, lambda: None)  # 测试替身/页已销毁：不抛


# ------------------------------------------------------------ keeper 窗口发现


class _CtxPages:
    def __init__(self, pages):
        self.pages = pages

    async def new_page(self):
        p = _Pg(url="")
        self.pages.append(p)
        return p


def test_find_blank_default_tab():
    ctx = _CtxPages([_Pg(url="https://x.com/a"), _Pg(url="about:blank")])
    assert sw._find_blank_default_tab(ctx).url == "about:blank"
    assert sw._find_blank_default_tab(_CtxPages([_Pg(url="https://x.com/a")])) is None


def test_pick_initial_shell_page_prefers_existing():
    base = "http://127.0.0.1:8791/"
    ctx = _CtxPages([_Pg(url=base), _Pg(url="about:blank")])
    assert asyncio.run(sw._pick_initial_shell_page(ctx, base)).url == base


def test_pick_initial_shell_page_reuses_blank():
    base = "http://127.0.0.1:8791/"
    ctx = _CtxPages([_Pg(url="about:blank")])
    got = asyncio.run(sw._pick_initial_shell_page(ctx, base))
    assert got.url == "about:blank"
    assert len(ctx.pages) == 1  # 复用而非新开


def test_pick_initial_shell_page_creates_when_none():
    base = "http://127.0.0.1:8791/"
    ctx = _CtxPages([_Pg(url="https://x.com/a")])
    got = asyncio.run(sw._pick_initial_shell_page(ctx, base))
    assert got in ctx.pages
    assert len(ctx.pages) == 2


def test_keeper_shell_effective():
    ctx = _CtxPages([])
    c = sw.WindowCoordinator()
    assert sw._keeper_shell_effective(c, ctx) is None  # 未登记

    p = _Pg(url="u")
    ctx.pages.append(p)
    c.register_shell(p)
    assert sw._keeper_shell_effective(c, ctx) is p

    ctx.pages.clear()  # 会话重建（崩溃恢复）：引用不再属于当前页面集
    assert sw._keeper_shell_effective(c, ctx) is None


def test_keeper_find_shell_prefers_coordinator():
    """壳窗正被守卫导航到登录页（URL 不含 base）时，不能误判「壳窗被关」。"""
    base = "http://127.0.0.1:8791/"
    ctx = _CtxPages([])
    c = sw.WindowCoordinator()
    p = _Pg(url="https://www.xiaohongshu.com/login")
    ctx.pages.append(p)
    c.register_shell(p)
    assert asyncio.run(sw._keeper_find_shell(ctx, base, c)) is p


def test_keeper_find_shell_scans_base_and_registers():
    base = "http://127.0.0.1:8791/"
    ctx = _CtxPages([_Pg(url=base + "index")])
    c = sw.WindowCoordinator()
    got = asyncio.run(sw._keeper_find_shell(ctx, base, c))
    assert got is not None
    assert c.shell() is got


def test_keeper_find_shell_none():
    assert asyncio.run(
        sw._keeper_find_shell(_CtxPages([]), "http://b/", sw.WindowCoordinator())) is None


def test_match_collect_page():
    c = sw.WindowCoordinator()
    shell = _Pg(url="u")
    assert sw._match_collect_page(shell, shell, c) is None  # 壳窗本身不算采集窗
    assert sw._match_collect_page(_Pg(url="https://x.com/"), shell, c) is None
    p = _Pg(url="https://www.xiaohongshu.com/explore")
    assert sw._match_collect_page(p, shell, c) is p
    assert c.collect_win() is p


def test_keeper_find_collect_win_registered_first():
    ctx = _CtxPages([])
    c = sw.WindowCoordinator()
    shell = _Pg()
    win = _Pg(url="https://www.xiaohongshu.com/explore")
    ctx.pages.append(win)
    c.register_collect_win(win)
    assert asyncio.run(sw._keeper_find_collect_win(ctx, shell, c)) is win


def test_keeper_find_collect_win_never_returns_shell():
    """壳窗绝不能被当成采集窗，否则关主窗会被误判成「采集窗口已关闭」。"""
    shell = _Pg(url="https://www.xiaohongshu.com/explore")
    ctx = _CtxPages([shell])
    c = sw.WindowCoordinator()
    assert asyncio.run(sw._keeper_find_collect_win(ctx, shell, c)) is None


def test_keeper_find_collect_win_scans_pages():
    ctx = _CtxPages([_Pg(), _Pg(url="https://www.xiaohongshu.com/explore")])
    c = sw.WindowCoordinator()
    shell = ctx.pages[0]
    got = asyncio.run(sw._keeper_find_collect_win(ctx, shell, c))
    assert got is ctx.pages[1]


# ------------------------------------------------------------ 采集窗创建


class _PgFull(_Pg):
    """创建流程用页面：覆盖 wait_for_load_state / bring_to_front / CDP。"""

    def __init__(self, url="https://www.xiaohongshu.com/explore"):
        super().__init__(url=url)
        self.load_calls = 0
        self.fronted = False

    async def wait_for_load_state(self, state=None, timeout=None):
        self.load_calls += 1

    async def bring_to_front(self):
        self.fronted = True

    async def add_init_script(self, js):
        return None

    async def evaluate(self, js):
        return "ok"

    async def new_cdp_session(self, page):
        return _CdpSession()

    @property
    def context(self):
        return self


class _Awaitable:
    """模拟 Playwright 的 pinfo.value（可 await 一次的值）。"""

    def __init__(self, v):
        self._v = v

    def __await__(self):
        async def _g():
            return self._v

        return _g().__await__()


class _PInfo:
    def __init__(self, page):
        self.value = _Awaitable(page)


class _ExpectPage:
    def __init__(self, page):
        self._p = page

    async def __aenter__(self):
        return _PInfo(self._p)

    async def __aexit__(self, *a):
        return False


class _CtxExpect:
    def __init__(self, page, new_page=None):
        self._page = page
        self._new_page = new_page

    def expect_page(self, timeout=None):
        return _ExpectPage(self._page)

    async def new_page(self):
        return self._new_page


class _ShellEval:
    def __init__(self):
        self.calls = []

    async def evaluate(self, js, arg=None):
        self.calls.append((js, arg))
        return None


def _pin_geometry(monkeypatch):
    from src import engine_pool
    monkeypatch.setattr(engine_pool, "shell_window_geometry",
                        lambda: (960, 800, 0, 960))


def test_open_collect_window_uses_plain_window(monkeypatch):
    """R-6.83 用户拍板：采集调试窗要标准浏览器外观（地址栏/标签栏）。
    带 CFT 路径时必须走 `--new-window` 命令行路由，绝不能再走 `--app`。"""
    _pin_geometry(monkeypatch)
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)

    seen = {}

    async def _fake_plain(ctx, url, **kw):
        seen["kw"] = kw
        return _PgFull(url)

    async def _boom(ctx, url, **kw):
        raise AssertionError("R-6.83 后采集窗不再走 --app 应用模式")

    monkeypatch.setattr(sw, "_launch_plain_window", _fake_plain)
    monkeypatch.setattr(sw, "_launch_app_window", _boom)
    shell = _ShellEval()
    p = asyncio.run(sw.open_collect_window(
        _CtxExpect(None), shell, url="https://www.xiaohongshu.com/explore",
        user_data_dir="/tmp/udd", executable="/tmp/cft"))
    assert isinstance(p, _PgFull)
    assert seen, "必须走 --new-window 命令行路由"
    assert seen["kw"].get("user_data_dir") == "/tmp/udd"
    assert shell.calls == [], "走命令行路由时不该退回 window.open"
    assert p.load_calls == 1
    assert p.fronted is True          # 建完置前，用户能看见


def test_open_collect_window_falls_back_to_window_open(monkeypatch):
    """没有 CFT 路径（无命令行路由）：退回 window.open 开独立窗口。"""
    _pin_geometry(monkeypatch)
    monkeypatch.setattr(sw, "cdp_call", _fake_cdp_call)
    page = _PgFull()
    shell = _ShellEval()
    p = asyncio.run(sw.open_collect_window(_CtxExpect(page), shell))
    assert p is page
    assert shell.calls, "兜底路径必须真的窗口化"
    assert "window.open" in shell.calls[0][0]


def test_open_collect_window_falls_back_to_new_page(monkeypatch):
    """window.open 没吐出新页（被拦/弹窗策略吃掉）：退化到引擎内新页，不抛给上层。"""
    _pin_geometry(monkeypatch)
    fallback = _PgFull()
    shell = _ShellEval()
    p = asyncio.run(sw.open_collect_window(
        _CtxExpect(None, new_page=fallback), shell))
    assert p is fallback


def test_launch_app_window_success(monkeypatch):
    """--app 路由成功：Popen 起新窗口，expect_page 抓回来。"""
    page = _PgFull()
    monkeypatch.setattr(sw.subprocess, "Popen", lambda *a, **k: None)
    got = asyncio.run(sw._launch_app_window(
        _CtxExpect(page), "https://www.xiaohongshu.com/explore",
        user_data_dir="/tmp/u", executable="/tmp/c",
        left=960, width=960, height=800))
    assert got is page


def test_launch_app_window_returns_none_after_retries(monkeypatch):
    """引擎 profile 初始化期 Popen 失败：重试 3 次后返回 None，由调用方兜底。"""
    async def _noop(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)

    def boom(*a, **k):
        raise PermissionError("profile 目录初始化中")

    monkeypatch.setattr(sw.subprocess, "Popen", boom)
    got = asyncio.run(sw._launch_app_window(
        _CtxExpect(None), "https://www.xiaohongshu.com/explore",
        user_data_dir="/tmp/u", executable="/tmp/c",
        left=0, width=100, height=100))
    assert got is None


# ------------------------------------------------------------ Cookie 读取


class _PgCookies(_Pg):
    def __init__(self, url, cookies, boom=False):
        super().__init__(url=url)
        self._cookies = cookies
        self._boom = boom
        self.context = self

    async def cookies(self):
        if self._boom:
            raise RuntimeError("上下文没了")
        return self._cookies


def test_login_cookies_on_browsing_page():
    cs = [{"name": "web_session", "domain": ".xiaohongshu.com"},
          {"name": "other", "domain": ".example.com"}]
    got = asyncio.run(sw._login_cookies_on_browsing_page(
        _PgCookies("https://www.xiaohongshu.com/explore", cs)))
    assert len(got) == 1 and got[0]["name"] == "web_session"


def test_login_cookies_ignored_while_still_on_login():
    """还停在 /login 时的 Cookie 不算「扫码成功」（否则排障会被假信号带偏）。"""
    cs = [{"name": "web_session", "domain": ".xiaohongshu.com"}]
    assert asyncio.run(sw._login_cookies_on_browsing_page(
        _PgCookies("https://www.xiaohongshu.com/login", cs))) == []


def test_login_cookies_require_web_session():
    """浏览页但只有非会话 Cookie → 不算登录。"""
    assert asyncio.run(sw._login_cookies_on_browsing_page(
        _PgCookies("https://www.xiaohongshu.com/explore",
                   [{"name": "a1", "domain": ".xiaohongshu.com"}]))) == []


def test_xhs_cookies_swallows_context_error():
    assert asyncio.run(sw._xhs_cookies(_PgCookies("u", [], boom=True))) == []


# ------------------------------------------------------------ 重复页去重


def test_duplicate_xhs_pages():
    keep = _Pg(url="https://www.xiaohongshu.com/explore")
    a = _Pg(url="https://www.xiaohongshu.com/explore")
    b = _Pg(url="https://www.xiaohongshu.com/explore/#x")  # 归一后同 key
    other = _Pg(url="https://www.xiaohongshu.com/login")
    ctx = _CtxPages([keep, a, b, other])
    dups = sw._duplicate_xhs_pages(ctx, keep)
    assert keep not in dups        # 已登记的那页优先保留
    assert a in dups and b in dups
    assert other not in dups       # URL 不同不算重复


# ------------------------------------------------------------ 空白页清理


class _PgClose(_Pg):
    def __init__(self, url="", closed=False):
        super().__init__(url=url)
        self._closed = closed
        self.closed_calls = 0

    def is_closed(self):
        return self._closed

    async def close(self):
        self.closed_calls += 1


def test_close_if_blank_only_closes_blank_pages():
    blank = _PgClose(url="about:blank")
    assert asyncio.run(sw._close_if_blank(blank, "about:blank")) is True
    assert blank.closed_calls == 1

    login = _PgClose(url="https://www.xiaohongshu.com/login")
    assert asyncio.run(sw._close_if_blank(login, login.url)) is False
    assert login.closed_calls == 0  # 登录页是扫码入口，keeper 绝不主动关


def test_keeper_sweep_blank():
    shell = _PgClose(url="http://127.0.0.1:8791/")
    blank = _PgClose(url="about:blank")
    newtab = _PgClose(url="chrome://newtab")
    login = _PgClose(url="https://www.xiaohongshu.com/login")
    already = _PgClose(url="about:blank", closed=True)
    ctx = _CtxPages([shell, blank, newtab, login, already])
    n = asyncio.run(sw._keeper_sweep_blank(ctx, shell))
    assert n == 2                     # 只关 blank + newtab
    assert shell.closed_calls == 0    # 壳窗不能关
    assert login.closed_calls == 0    # 扫码入口不能关
    assert already.closed_calls == 0  # 已关闭的不重复处理


def test_keeper_recovery_cleanup_keeps_shell_only():
    """崩溃恢复后只留主窗口（用户拍板：关掉之前未关闭的窗口）。"""
    shell = _PgClose()
    others = [_PgClose(), _PgClose()]
    ctx = _CtxPages([shell, *others])
    n = asyncio.run(sw._keeper_recovery_cleanup(ctx, shell))
    assert n == 2
    assert shell.closed_calls == 0


def test_safe_close_swallows_error():
    class _Boom:
        async def close(self):
            raise RuntimeError("已经关了")

    asyncio.run(sw._safe_close(_Boom()))  # 不抛


def test_keeper_log_snapshot_only_when_changed():
    ctx = _CtxPages([_PgClose(url="a")])
    c = sw.WindowCoordinator()
    last = [frozenset()]
    got = sw._keeper_log_snapshot(ctx, c, last)
    assert got == frozenset({"a"})
    sw._keeper_log_snapshot(ctx, c, last)  # 页面集没变：不重复打日志
    assert last[0] == frozenset({"a"})
