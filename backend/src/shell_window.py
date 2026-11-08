#!/usr/bin/env python3
"""路线 A 壳窗口：CFT（Chrome for Testing）承载 APP 前端 —— CDP 可控的可见 APP 窗口。

背景（R-6.16 用户拍板 A 路径）：
- 旧壳 = pywebview（WKWebView，macOS cocoa 后端），**不支持 CDP**——UI 自动化
  （CDP 9223）只能驱动引擎里打开的同一套前端，用户可见的 APP 窗口无自动化发生。
- 新壳 = 引擎 CFT 窗口承载前端：打开 APP = 打开一个 Chrome for Testing 窗口，
  里面显示我们自己的前端界面；CDP 直接驱动这个窗口（填词/点击/采集全程可见）；
  采集小红书也在同一个 CFT 里开 tab 干活（登录态/数据/引擎全复用）。

职责：
- ensure_shell(base_url)：强制引擎有头启动 + 打开前端 tab（bring_to_front），
  返回 ShellWindow（page 封装，登录守卫的载体）。
- login_guard(win, base_url, stop)：从旧 launcher 整体迁移的登录守卫
  （R-6.5→R-6.10 语义不变），API 载体从 pywebview window 换成 ShellWindow(page)。
- 修复历史潜伏 bug：_OPEN_LOGIN_PANEL_JS 长期悬空引用（R-6.10 起从未定义，
  NameError 被 try 吞掉、静默降级整页登录）——本次重建定义（尽力注入，失败仍
  走整页登录页，不新增风险）。
"""
import asyncio
import json
import os
import subprocess
import time
import urllib.request
import json as _json_mod

# JS 注入脚本统一注册表（R-6.30a）：本模块历史上 5 个 JS 常量 + login_guard 内 2 个
# 局部 JS 已全部上提至 src/injections.py（单一事实源，防「悬空引用」类历史 bug 重现）。
from src import injections  # noqa: E402
from src.cdp_timeouts import CDP_QUICK, cdp_call
from src import logger as xlog  # noqa: E402
log = xlog.get("shell")


def _json_body(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return _json_mod.loads(r.read().decode("utf-8"))


def _json_post(url: str, payload: dict, timeout: float):
    req = urllib.request.Request(
        url,
        data=_json_mod.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json_mod.loads(r.read().decode("utf-8"))


async def open_collect_window(
    ctx,
    shell,
    url: str = "https://www.xiaohongshu.com/explore",
    *,
    user_data_dir: str | None = None,
    executable: str | None = None,
):
    """R-6.21b 采集独立窗口：**应用模式**（无地址栏/无标签条）+ 调试窗口标题。

    双窗口形态：窗口一=APP/登录（壳窗口应用模式，keep_shell_tab 监护固定不可关）；
    窗口二=采集（应用模式独立 CFT 窗口，同引擎同 profile —— 登录态/Cookie 完全
    共享、CDP 可控），由设置开关 collect_tab_visible 控制开/关，被手动关闭时按
    开关状态由 keep_shell_tab 事件监护重建。

    应用模式实现：Chrome 无「为既有 target 创建 app 窗口」API，window.open 只能
    开普通窗口（带地址栏/标签条）。采用**命令行路由**：向已运行的引擎实例
    （同 --user-data-dir 单实例锁）传 `--app=<url>`，Chrome 会让现有实例弹出
    **应用模式新窗口**（实测 CFT 156 生效），Playwright 对同 context 新 target
    自动 attach（expect_page 捕获）。窗口尺寸沿用引擎 --window-size=1280,800。
    标题跟随 document.title——采集页为真实小红书页，注入轻量 title 守护显示
    「XHS Leads · 采集调试窗口」（SPA 导航重设 title 由 interval 覆盖）。
    命令行路由不可用时（无 CFT/实例未起）退回 window.open 普通窗口（能力兜底）。
    """
    page = None
    from src import engine_pool  # noqa: PLC0415（与文件内其他延迟导入一致）

    if executable and user_data_dir:
        # R-6.22 双窗口左右分屏：采集调试窗口放主屏右半（等宽并排不重叠）
        win_w, win_h, _, win_left = engine_pool.shell_window_geometry()
        page = await _launch_app_window(
            ctx, url, user_data_dir=user_data_dir, executable=executable,
            left=win_left, width=win_w, height=win_h,
        )
    if page is None:
        async with ctx.expect_page(timeout=8000) as pinfo:
            win_w, win_h, _, win_left = engine_pool.shell_window_geometry()
            await shell.evaluate(
                "(u) => window.open(u, 'xhs_collect', "
                f"'width={win_w},height={win_h},left={win_left},top=0')",
                url,
            )
        page = await pinfo.value
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=12000)
    except Exception:  # noqa: BLE001（采集骨架导航慢：不强求，bring_to_front 兜底）
        pass
    await _ensure_debug_title(page)
    # R-6.22 双窗口左右分屏：采集调试窗口放主屏右半（等宽并排不重叠）
    win_w, win_h, _, win_left = engine_pool.shell_window_geometry()
    await _calibrate_window(page, win_left, 0, win_w, win_h)
    try:
        await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                       label="shell.bring_to_front", skip=True)
    except Exception:  # noqa: BLE001
        pass
    return page


async def _ensure_shell_title(page) -> None:
    """壳窗口（主窗口）标题守护：无论 APP 页还是守卫导航的小红书登录页，
    Chrome 窗口标题恒定「XHS Leads · 小红书获客助手」（用户 R-6.48h 拍板：
    主窗口标题统一一致，不随页面状态变化）。与 _ensure_debug_title 同法：
    page 级 add_init_script（对后续导航自动生效）+ 当前文档立即生效；
    Python 侧 _shell_title_installed 标志防重，幂等可反复调用。
    """
    _title_js = injections.get("shell_title")
    if getattr(page, "_shell_title_installed", False):
        try:
            await cdp_call(page.evaluate(_title_js), timeout=CDP_QUICK,
                           label="shell.title_evaluate", skip=True)
        except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
            pass
        return
    try:
        await page.add_init_script(_title_js)
        await cdp_call(page.evaluate(_title_js), timeout=CDP_QUICK,
                       label="shell.title_init_evaluate", skip=True)
        page._shell_title_installed = True
    except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
        pass


async def _ensure_debug_title(page) -> None:
    """采集调试窗口标题守护：Chrome 窗口标题跟随 document.title。采集页为真实
    小红书页（SPA 路由/整页导航都会重设 title），用事件级守护（MutationObserver）
    覆盖为「XHS Leads · 采集调试窗口」，让用户一眼看出这是调试/观察窗口。
    title 为纯浏览器元数据：不触发网络请求、不改页面逻辑与 DOM 结构，
    风控面极小（远小于既有 stealth 注入）。

    采用 **page 级 add_init_script**（Playwright 仅对该 page 的后续导航生效，
    不污染壳窗口/其他页面）+ 当前文档立即生效；防重标记避免重复 add 累积。
    幂等，可反复调用（已装时仅刷新当前文档）。
    """
    _title_js = injections.get("collect_debug_title")
    if getattr(page, "_debug_title_installed", False):
        try:
            await cdp_call(page.evaluate(_title_js), timeout=CDP_QUICK,
                           label="shell.title_evaluate", skip=True)
        except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
            pass
        return
    try:
        await page.add_init_script(_title_js)
        await cdp_call(page.evaluate(_title_js), timeout=CDP_QUICK,
                       label="shell.title_init_evaluate", skip=True)
        page._debug_title_installed = True
    except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
        pass


# 真实登录页（R-6.5→R-6.6 强制登录）：直接导航小红书**独立整页登录页**。
# R-6.53z6：去掉原 `?redirectPath=...` 后缀——未登录态实测该参数对跳转毫无作用
# （带与不带都停在 /login），只会把脏参数带到地址栏。登录成功后由守卫捕获
# web_session 注入引擎再回 APP，不依赖平台自动跳转。
# 注：`/website-login` 实测会跳到 oia.xiaohongshu.com/oia（营销页），不可用。
XHS_LOGIN_URL = "https://www.xiaohongshu.com/login"

# 注：以下 JS 已全部上提至 injections 注册表（R-6.30a），按名取用：
#   collect_debug_title（采集调试窗口标题守护，page init script）
#   waiting_overlay / remove_waiting_overlay（登录成功过渡遮罩/移除）
#   active_close（主动重登「✕」，__BASE__ 占位符调用方替换）
#   open_login_panel（首页「登录」面板点击，原 R-6.10 悬空引用隐患已随注册表消除）
#   force_login / login_fit（原 login_guard 局部 JS，见函数体）


class ShellWindow:
    """CFT 引擎内前端 tab 的薄封装——登录守卫的 window-like 载体。

    API 对齐旧 pywebview window 的守卫所需子集：evaluate_js / load_url /
    current_url / cookies；实现全部走 playwright Page/Context。
    """

    def __init__(self, page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def rebind(self, page) -> None:
        """R-6.19：keeper 重建壳 tab 后重绑定 page（登录守卫继续使用新 tab）。"""
        self.page = page

    async def evaluate_js(self, js: str) -> str:
        try:
            return str(await self.page.evaluate(js))
        except Exception:  # noqa: BLE001（页面未就绪/导航中等，守卫外层自行处理）
            return ""

    async def load_url(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")

    async def current_url(self) -> str:
        return self.page.url

    async def cookies(self) -> list:
        # Playwright cookie dict：name/value/domain/path/secure/httpOnly/sameSite/expires
        # ——与后端 /api/auth/import_cookies 期望格式一致，无需再转换。
        return await self.page.context.cookies()

    async def clear_domain_cookies(self, domain_frag: str) -> int:
        """删除壳窗口（登录页所在窗口）指定域 Cookie，返回删除条数。

        R-6.22g：风控/已登录场景导航登录页前，登录页会因 Cookie 仍有效**自动跳过
        登录界面**（用户无法扫码验证解除风控）。先清除 web_session 使登录页呈现
        扫码界面，完成主动重新登录/验证。
        """
        cs = await self.cookies()
        removed = 0
        for c in cs:
            if domain_frag in c.get("domain", ""):
                try:
                    await self.page.context.clear_cookies(
                        name=c.get("name"), domain=c.get("domain"),
                        path=c.get("path", "/"),
                    )
                    removed += 1
                except Exception:  # noqa: BLE001（单条删除失败不影响整体）
                    pass
        return removed


# ---------------------------------------------------------------------------
# R-6.30c 轻量窗口协调器：shell/collect_win page 引用共享中心
# ---------------------------------------------------------------------------
class WindowCoordinator:
    """keeper / guard / engine_pool 共用的窗口引用中心（轻量，不引入事件总线/状态机）。

    - keeper（keep_shell_tab）是引用的**写入方**：发现/重建/创建 shell 与 collect_win 时
      register_* 登记，并 watch() 幂等挂载 close 回调；
    - guard（login_guard）与 engine_pool.cleanup_tabs 是**读取方**：shell()/collect_win()
      判活取引用，不再各自遍历 ctx.pages 找窗口（窗口竞态/震荡的历史根源）；
    - 引用失效：page 关闭后判活返回 None，由 keeper 按需重建/重新登记；
    - 命令式方法：navigate_collect_win / close_orphan_explore。
    """

    def __init__(self):
        self._shell = None   # 壳窗 page 引用（登录导航 URL 变化不影响判活）
        self._collect_win = None    # 采集窗（采集窗）page 引用
        self._watched: set = set()  # 已挂 close 监听的 page（对象引用去重，幂等）
        self._stop = None
        self._on_shell_closed = None
        self._engine = None  # R-6.53c：引擎实例（close 事件校验「用户关窗 vs 引擎崩溃」）
        self._shell_close_ts = 0.0  # R-6.53f：壳窗 close 事件时间戳（0=无记录）
        self._loop = None  # R-6.53g：keeper 事件循环（bind 时捕获，close 回调在
        # playwright 驱动线程，get_event_loop/get_running_loop 均不可靠）

    # ---------------- 回调绑定（keeper 启动时一次） ----------------
    def bind(self, stop, on_shell_closed) -> None:
        self._stop = stop
        self._on_shell_closed = on_shell_closed
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def attach_engine(self, engine) -> None:
        """R-6.53c：绑定账号引擎实例，供壳窗 close 事件校验引擎连接状态。"""
        self._engine = engine

    def shell_close_age(self) -> float:
        """距最近一次壳窗 close 事件的秒数（无记录返回极大值），供 keeper 轮询
        兜底区分「用户关窗」与「引擎崩溃」——不依赖 close 回调的异步调度。"""
        if self._shell_close_ts <= 0:
            return 1e18
        return time.monotonic() - self._shell_close_ts

    async def _engine_alive_after_close(self) -> bool:
        """壳窗 close 后判定「引擎是否仍健康」：重建期/断连/校验异常一律 False。

        False 的语义是**跳过退出**（崩溃恢复交给 keeper 重建），只在引擎确认
        存活时返回 True。R-6.53c / R-6.53h 的判定集中在此，便于单测覆盖。
        """
        eng = self._engine
        if eng is None:
            return True
        try:
            # attach_engine 传入 EnginePool 池对象 → 取账号引擎实例
            account = await eng.get("default") if hasattr(eng, "get") else eng
        except Exception:  # noqa: BLE001（引擎未就绪：按存活处理走退出兜底）
            return True
        if account is None:
            return True
        try:
            # R-6.53h：对象类型防御——校验异常时保守不退出，交给 keeper 轮询兜底，
            # 避免「崩溃恢复被误判用户关窗退出应用」（历史：误传模块导致 AttributeError）
            if account.rebuild_age() < 60:
                log.warning("壳窗 close 校验：引擎 60s 内重建过（崩溃恢复），跳过退出")
                return False
            if not account.is_connected_now():
                log.warning("壳窗 close 校验：引擎连接已断开（崩溃），跳过退出")
                return False
        except Exception:  # noqa: BLE001（对象无校验方法/异常：无法确认引擎状态，保守不退出）
            log.warning("壳窗 close 校验：引擎状态校验异常，保守跳过退出")
            return False
        return True

    async def _shell_closed_verify(self) -> None:
        """R-6.53c：壳窗 close 事件延迟 3s 校验——引擎崩溃时壳窗 close 是引擎
        进程死亡的副作用（非用户行为），跳过退出交给 keeper 重建；引擎存活且
        非重建期才是用户真关主窗，维持「关主窗=退出应用」语义。"""
        if self._stop is not None and self._stop.is_set():
            return
        if not await self._engine_alive_after_close():
            return
        if self._on_shell_closed is not None:
            log.info("壳窗被关闭（引擎存活），触发应用退出")
            self._on_shell_closed()

    # ---------------- 判活引用 ----------------
    @staticmethod
    def _alive(page):
        if page is None:
            return None
        try:
            return None if page.is_closed() else page
        except Exception:  # noqa: BLE001（page 对象异常视同失效）
            return None

    def shell(self):
        """壳窗引用（判活）；未登记/已关闭返回 None。"""
        return self._alive(self._shell)

    def collect_win(self):
        """采集窗引用（判活）；未登记/已关闭返回 None。"""
        return self._alive(self._collect_win)

    def register_shell(self, page) -> None:
        self._shell = page

    def register_collect_win(self, page) -> None:
        self._collect_win = page

    @staticmethod
    def _bind_page_events(page, on_close, on_crash) -> None:
        """挂 close/crash 监听（事件不支持时静默：仍由 close/轮询兜底）。"""
        try:
            # R-6.22j 取证：记录壳窗 close 时的 URL，定位「壳窗被关」来源
            page.on("close", lambda: log.info(
                f"壳窗 close 事件（url={page.url!r}）"))
            page.on("close", on_close)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.on("crash", on_crash)
        except Exception:  # noqa: BLE001（事件不支持时静默：真实场景仍由 close/轮询兜底）
            pass

    def _handle_close(self, page, is_shell: bool) -> None:
        """close 事件：壳窗记时间并延迟校验；采集窗仅打印（应用保持运行）。"""
        if self._stop is not None and self._stop.is_set():
            return
        if not is_shell:
            log.info("右侧调试窗被关闭（应用保持运行，按需重建）")
            self._schedule_collect_win_rebuild()
            return
        # R-6.53c：引擎崩溃时壳窗 close 事件随引擎进程死亡触发，不能立即退出——
        # 延迟 3s 校验引擎状态，区分「用户关主窗」与「崩溃」。
        self._shell_close_ts = time.monotonic()  # R-6.53f：供轮询兜底
        log.warning("壳窗 close 事件，3s 后校验（区分用户关闭/引擎崩溃）")
        # R-6.53g：close 回调在 playwright 驱动线程，必须用 bind 时捕获的
        # keeper loop 调度；无 loop 时仅靠轮询 shell_close_age 兜底。
        if self._loop is None:
            return
        try:
            self._loop.call_later(3.0, lambda: asyncio.ensure_future(
                self._shell_closed_verify()))
        except Exception:  # noqa: BLE001（loop 关闭中：keeper 轮询兜底）
            pass


    def _collect_win_should_rebuild(self) -> bool:
        """右窗关闭后是否重建：登录流程进行中视为「用户取消登录」，不重建。"""
        try:
            from src import server as _server  # noqa: PLC0415（延迟导入规避循环依赖）

            return not bool(_server._LOGIN_GATE.get("pending"))
        except Exception:  # noqa: BLE001（状态源不可用：按需要重建处理）
            return True

    def _schedule_collect_win_rebuild(self) -> None:
        """右窗关闭后的重建调度（一次性定时，不是周期轮询）。

        采集中 → 立即重建（采集不能没有窗口）；空闲 → 15s 后重建（用户口径
        「关掉了左边也能打开它」，留出误关的反悔窗口）。
        """
        if self._loop is None or not self._collect_win_should_rebuild():
            return
        delay = 0.0 if _server_has_running_collect() else 15.0
        try:
            self._loop.call_later(delay, lambda: asyncio.ensure_future(self._rebuild_collect_win_once()))
        except Exception:  # noqa: BLE001（loop 关闭中：不重建）
            pass

    @xlog.tagged("win:collect:rebuild")
    async def _rebuild_collect_win_once(self) -> None:
        """真正重建右窗：已存在则跳过（15s 窗口内用户可能已手动开回来）。"""
        if self._stop is not None and self._stop.is_set():
            return
        if self.collect_win() is not None:
            return
        try:
            from src import config, engine_pool  # noqa: PLC0415

            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            p = await ctx.new_page()
            self.register_collect_win(p)
            self.watch(p, is_shell=False)
            _watch_collect_win_login(p)
            url = await _keeper_collect_target_url(config, engine_pool)
            await p.goto(url, wait_until="domcontentloaded", timeout=8000)
            log.info(f"右侧调试窗已重建（{url}）")
        except Exception as e:  # noqa: BLE001（重建失败下次关闭事件再试，不做周期重试）
            log.warning(f"右侧调试窗重建失败: {e!r}")
    def _handle_crash(self, page, is_shell: bool) -> None:
        """R-6.48o：壳窗口渲染器崩溃（Chrome 弹崩溃对话框→会话恢复，
        App 不退出）→ 延迟恢复清理：关闭其余窗口，崩溃页留给 Chrome 恢复。"""
        if not is_shell:
            return
        if self._stop is not None and self._stop.is_set():
            return
        log.warning("壳窗口崩溃，触发残留窗口清理")
        try:
            loop = asyncio.get_event_loop()
            loop.call_later(3.0, lambda: asyncio.ensure_future(
                self._recovery_cleanup_safe(page)))
        except Exception:  # noqa: BLE001
            pass

    def watch(self, page, is_shell: bool) -> None:
        if any(p is page for p in self._watched):
            return
        self._watched.add(page)

        def _on_close() -> None:
            try:
                self._handle_close(page, is_shell)
            except Exception:  # noqa: BLE001（loop 关闭中：兜底轮询接管）
                pass

        def _on_crash() -> None:
            try:
                self._handle_crash(page, is_shell)
            except Exception:  # noqa: BLE001
                pass

        self._bind_page_events(page, _on_close, _on_crash)

    async def _recovery_cleanup_safe(self, page):
        """壳窗口崩溃后的恢复清理（延迟执行）：等 Chrome 会话恢复稳定后，
        关闭非崩溃页的全部窗口，崩溃页本身保留由 Chrome 恢复。"""
        try:
            await asyncio.sleep(3.0)
            ctx = page.context
            n = await _keeper_recovery_cleanup(ctx, page)
            if n:
                log.warning(f"崩溃恢复：关闭 {n} 个残留窗口，仅保留主窗口")
        except Exception:  # noqa: BLE001（引擎已死/重连中：兜底轮询接管）
            pass

    # ---------------- 命令式方法 ----------------
    async def navigate_collect_win(self, url: str, timeout: int = 8000) -> None:
        """采集窗 导航到指定 URL（引用失效时静默跳过——keeper 下轮重建后再管）。"""
        t2 = self.collect_win()
        if t2 is None:
            return
        await t2.goto(url, wait_until="domcontentloaded", timeout=timeout)


def _is_orphan_explore_url(u: str) -> bool:
    """R-6.37i：URL 是否孤儿 explore 页（小红书域且非 /login）。"""
    return "xiaohongshu.com" in u and "/login" not in u


async def _close_if_orphan_explore(p, shell, t2) -> None:
    """R-6.37i 拆自 close_orphan_explore：单页若为孤儿 explore 则关闭（壳/采集窗 保留）。"""
    if p is shell or p is t2 or p.is_closed():
        return
    if _is_orphan_explore_url(p.url or ""):
        try:
            await p.close()
        except Exception:  # noqa: BLE001
            pass


    async def close_orphan_explore(self, ctx) -> None:
        """未登录时关闭孤儿 explore 判定页（login_status 轮询可能新开）；
        壳窗与 采集窗 引用无条件保留。"""
        shell, t2 = self.shell(), self.collect_win()
        for p in list(ctx.pages):
            await _close_if_orphan_explore(p, shell, t2)


_COORD: WindowCoordinator | None = None


def get_coordinator() -> WindowCoordinator:
    """模块级协调器单例（keeper 写入 / guard 与 engine_pool 读取共用同一对象）。"""
    global _COORD
    if _COORD is None:
        _COORD = WindowCoordinator()
    return _COORD


def current_shell_page():
    """engine_pool.cleanup_tabs 读壳窗引用的模块入口（R-6.30c 修复：历史
    shell_window.shell_ref 为 keeper 局部变量、模块属性不存在 → AttributeError 被
    吞掉，壳窗保护从未生效）。协调器未就绪（keeper 未启动）返回 None。"""
    return _COORD.shell() if _COORD is not None else None


async def _launch_app_window(
    ctx, url: str, *, user_data_dir: str, executable: str,
    left: int, width: int, height: int,
):
    """R-6.22c 应用模式窗口（无菜单栏/标签栏/地址栏）：--app 命令行路由。

    Chrome 无「为既有 target 创建 app 窗口」API；向已运行实例（同
    --user-data-dir 单实例锁）传 `--app=<url>`，现有实例会弹出应用模式新窗口，
    Playwright 对同 context 新 target 自动 attach（expect_page 捕获）。
    路由不可用（无 CFT/exec 失败）返回 None，调用方退普通窗口兜底。
    引擎启动初期（profile 目录初始化中）Popen 偶发 PermissionError——重试
    至多 3 次（间隔 1.5s），引擎就绪后 Popen 稳定成功（实测）。
    """
    page = None
    if executable and user_data_dir:
        for attempt in range(3):
            try:
                async with ctx.expect_page(timeout=8000) as pinfo:
                    subprocess.Popen(
                        [
                            executable,
                            f"--user-data-dir={user_data_dir}",
                            f"--app={url}",
                            f"--window-size={width},{height}",
                            f"--window-position={left},0",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                page = await pinfo.value  # async API：EventInfo.value 是 coroutine，须 await
                break
            except Exception as e:  # noqa: BLE001（路由失败：重试，仍失败则调用方兜底）
                import traceback as _tb
                log.warning(
                    f"--app 窗口路由失败（第{attempt + 1}/3 次）: {e!r}\n"
                    f"  cwd={os.getcwd()} executable={executable!r}\n"
                    f"  user_data_dir={user_data_dir!r} url={url!r}\n"
                    f"  {''.join(_tb.format_exception_only(type(e), e)).strip()}",

                )
                await asyncio.sleep(1.5)
    return page


async def _calibrate_window(page, left: int, top: int, width: int, height: int) -> None:
    """R-6.22 双窗口左右分屏：CDP Browser.setWindowBounds 精确摆位/定尺寸。

    launch_persistent_context 的 viewport 会覆盖 --window-size（实测窗口仍 1280
    宽），但 --window-position 生效；故用 CDP 后置校准窗口 bounds：壳窗口放
    主屏左半 (0,0)、采集调试窗口放右半 (win_left,0)，等宽并排互不重叠。
    R-6.27：page 刚创建时 CDP session 偶发 guid 失效（TargetClosedError），
    失败静默重试 3 次（间隔 1s）再放弃。
    """
    last_err = None
    for attempt in range(3):
        try:
            cdp = await page.context.new_cdp_session(page)
            t = await cdp.send("Browser.getWindowForTarget")  # 缺省 targetId = 当前 session 页面
            await cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": t["windowId"],
                    "bounds": {
                        "left": left, "top": top, "width": width, "height": height,
                        "windowState": "normal",
                    },
                },
            )
            return
        except Exception as e:  # noqa: BLE001（CDP 不可用不阻断启动/创建）
            last_err = e
            await asyncio.sleep(1.0)
    log.warning(
        f"窗口校准失败（{left},{top} {width}x{height}，"
        f"重试 3 次仍失败）: {last_err!r}",

    )


async def _goto_base_with_retry(page, base) -> None:
    """壳 tab 导航前端（uvicorn 刚起时静态路由未就绪 → 重试 ≤20s）。R-6.36e 拆自 ensure_shell。"""
    last_err = None
    for _ in range(40):
        try:
            await page.goto(base, wait_until="domcontentloaded")
            if (page.url or "").startswith(base):
                return
        except Exception as e:  # noqa: BLE001（后端就绪前 goto 会失败，下轮重试）
            last_err = e
        await asyncio.sleep(0.5)
    log.warning(f"壳 tab 导航前端未成功（最后错误: {last_err!r}）")


async def _close_matching_tabs(ctx, keep, predicate) -> None:
    """关闭 ctx 中满足 predicate 的非 keep tab（空白残留/重复壳 tab 清理通用助手）。R-6.36e 拆自 ensure_shell。"""
    for p in list(ctx.pages):
        if p is keep or p.is_closed():
            continue
        if predicate(p):
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                pass


async def _promote_shell_to_app_window(ctx, eng, base, win_w, win_h):
    """应用模式提升壳窗（--app 路由）：成功返回 app_page；不可用返回 None。R-6.36e 拆自 ensure_shell。"""
    from src.engine_types import builtin_cft_candidates  # noqa: PLC0415

    cfts = builtin_cft_candidates()
    app_page = await _launch_app_window(
        ctx, base,
        user_data_dir=eng.user_data_dir,
        executable=cfts if cfts else None,  # 返回值为单个路径 str（非列表），勿取 [0]
        left=0, width=win_w, height=win_h,
    )
    if app_page is None:
        return None
    try:
        await app_page.goto(base, wait_until="domcontentloaded", timeout=12000)
    except Exception:  # noqa: BLE001（--app=base 已带 URL，goto 仅保险）
        pass
    await _calibrate_window(app_page, 0, 0, win_w, win_h)
    try:
        await app_page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    # 关闭初始普通窗口 tab（keep_shell_tab 未启动无监听；后续监护 _find_shell 按 URL 找到应用模式壳窗口）
    await _close_matching_tabs(ctx, app_page, lambda p: (p.url or "").startswith(base))
    return app_page


def _find_blank_default_tab(ctx):
    """R-6.37i 拆自 _pick_initial_shell_page：复用 about:blank/空默认 tab。"""
    return next(
        (p for p in ctx.pages if not p.is_closed() and (p.url or "").strip() in ("", "about:blank")),
        None,
    )


async def _pick_initial_shell_page(ctx, base):
    """ensure_shell 初始页：优先已有壳 tab；否则复用 about:blank 默认 tab；无则新开。"""
    for p in ctx.pages:
        if (p.url or "").startswith(base) and not p.is_closed():
            return p
    page = _find_blank_default_tab(ctx)
    if page is None:
        page = await ctx.new_page()
    return page


async def ensure_shell(base_url: str):
    """强制引擎有头启动，打开前端 tab 并置前（壳窗口 = CFT 引擎窗口）。R-6.36e：逻辑块拆至上方三个助手。"""
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    if config.account_headless("default"):
        config.set_account_headless("default", False)  # 壳必须有头（A 路径常态）
    eng = await engine_pool.pool.get("default")
    ctx = await eng.context()
    base = base_url.rstrip("/")
    page = await _pick_initial_shell_page(ctx, base)
    await _goto_base_with_retry(page, base)
    # R-6.19：导航成功后清理其余空白残留 tab（启动默认页已复用，此处仅清理异常残留）
    await _close_matching_tabs(ctx, page, lambda p: (p.url or "").strip() in ("", "about:blank"))
    win_w, win_h, _, _ = engine_pool.shell_window_geometry()
    # R-6.22c：统一走 --app 命令行路由（与采集调试窗口同法）弹应用模式窗口；
    # 路由不可用时退回普通 tab（能力兜底），仅校准。
    app_page = await _promote_shell_to_app_window(ctx, eng, base, win_w, win_h)
    if app_page is not None:
        page = app_page
    else:
        await _calibrate_window(page, 0, 0, win_w, win_h)
    try:
        await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                       label="shell.bring_to_front", skip=True)
    except Exception:  # noqa: BLE001
        pass
    await _ensure_shell_title(page)
    return ShellWindow(page, base)


# ---------- login_guard 拆分助手（R-6.36e：行为与原内联闭包逐字等价） ----------

async def _ensure_login_injects(win: "ShellWindow") -> None:
    """登录页行为注入（force_login 弹窗锁 + login_fit 居中适配 + login_clean 净化）：
    page 级 add_init_script——浏览器在每个新 document（整页导航/SPA 重建）创建
    瞬间自动执行，JS 内部 MutationObserver 事件驱动；无守卫轮询补注
    （R-6.48k，用户拍板：循环守护不可取，直接注入行为）。
    幂等：已注册的仅对当前文档 evaluate 立即生效；防重标记避免重复 add 累积。
    evaluate 失败（页面导航中/未就绪）不重试轮询——下一事件点（导航登录页/
    重绑定/拉回）自然补注，且新 document 由 init script 自动生效。
    """
    for key in ("force_login", "login_fit", "login_clean"):
        js = injections.get(key)
        flag = f"_login_{key}_installed"
        if not getattr(win.page, flag, False):
            try:
                await win.page.add_init_script(js)
                setattr(win.page, flag, True)
                log.info(f"{key} init script 已注册")
            except Exception as e:  # noqa: BLE001
                log.warning(f"{key} add_init_script 失败: {e!r} url={win.page.url[:60]!r}")
        try:
            await win.page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            log.warning(f"{key} evaluate 失败: {e!r} url={win.page.url[:60]!r}")


async def _guard_inject_active_close(win: "ShellWindow", base: str) -> None:
    try:
        await win.evaluate_js(injections.get("active_close").replace("__BASE__", f"{base}/"))
    except Exception as e:  # noqa: BLE001
        log.info(f"主动重登关闭按钮注入暂未成功: {e!r}")


async def _guard_get_json(base: str, path: str):
    # R-6.16：同步 urllib 每 0.5s 轮询会阻塞整个 loop → asyncio.to_thread 脱离 loop。
    try:
        return await asyncio.to_thread(lambda: _json_body(base + path, timeout=3))
    except Exception:  # noqa: BLE001
        return None


async def _guard_post_json(base: str, path: str, payload: dict):
    try:
        return await asyncio.to_thread(lambda: _json_post(base + path, payload, timeout=15))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def _guard_navigate_to_login(win: "ShellWindow", base: str, cur_force: bool) -> None:
    """pending 检测后：清 Cookie（强制登录）→ 双保险导航登录页 → 开面板 + 注入适配/关闭钮。"""
    if cur_force:
        try:
            n = await win.clear_domain_cookies("xiaohongshu.com")
            log.info(f"强制登录前清除壳窗口小红书 Cookie {n} 条")
        except Exception as e:  # noqa: BLE001
            log.warning(f"清除壳窗口 Cookie 异常: {e!r}")
    try:
        await win.load_url(XHS_LOGIN_URL)
        log.warning("load_url 已调用（无异常）")
    except Exception as e:  # noqa: BLE001
        log.warning(f"load_url 异常: {e!r}")
    try:
        await win.evaluate_js(f"window.location.href = '{XHS_LOGIN_URL}'")
        log.info("evaluate_js 导航已下发（双保险）")
    except Exception as e:  # noqa: BLE001
        log.warning(f"evaluate_js 异常: {e!r}")
    try:
        await win.evaluate_js(injections.get("open_login_panel"))
    except Exception:  # noqa: BLE001（页面未就绪，下一事件点补注）
        pass
    await _ensure_login_injects(win)
    if not cur_force:
        await _guard_inject_active_close(win, base)


def has_id_token(cookies) -> bool:
    """登录态判定纯函数：cookies 中是否存在小红书 id_token（R-6.48n 统一 3 处内联）。"""
    try:
        return bool(cookies and any(c.get("name") == "id_token" for c in cookies))
    except TypeError:
        return False


_LOGIN_COOKIE_NAMES = ("id_token", "web_session")


def has_login_cookie(cookies) -> bool:
    """登录态判定（R-6.53z7）：id_token 或 web_session 任一存在即算已登录。

    只认 id_token 时，登录后只有 web_session 的会话会被判「未登录」，守卫随即把
    已跳到 explore 的页面拉回登录页（用户实测：登录成功又被弹回登录页）。
    """
    try:
        return bool(cookies and any(c.get("name") in _LOGIN_COOKIE_NAMES for c in cookies))
    except (TypeError, AttributeError):
        return False


async def _guard_try_import_cookies(win: "ShellWindow", base: str, cs, state) -> bool:
    """id_token 出现且满 0.5s → 注入引擎；成功返回 True。state 持 _t_last_import。"""
    if not has_login_cookie(cs):
        return False
    now = time.monotonic()
    if now - state["import"] < 0.5:
        return False
    state["import"] = now
    r = await _guard_post_json(base, "/api/auth/import_cookies", {"cookies": cs})
    return bool(r and r.get("logged_in"))


async def _guard_monitor_voluntary(win: "ShellWindow", base: str, cs, state) -> bool | None:
    """主动重登单步：返回 True=登录成功 / False=已关闭回 APP / None=继续监视。"""
    now = time.monotonic()
    if now - state["inject"] >= 5.0:
        state["inject"] = now
        await _guard_inject_active_close(win, base)
    if await _guard_try_import_cookies(win, base, cs, state):
        log.info("主动重登成功，Cookie 已注入引擎，返回 APP")
        return True
    try:
        cur = await win.current_url()
    except Exception:  # noqa: BLE001
        cur = ""
    if cur.startswith(base):
        log.info("主动重登已关闭（回 APP，登录态不变）")
        await _guard_post_json(base, "/api/auth/login_cancel", {})
        return False
    return None


async def _guard_handle_id_token(win, base, cs, state) -> bool | None:
    """R-6.36g 拆自 _guard_monitor_forced：捕获到 id_token 分支（等待遮罩→导入验证→成功/回退）。"""
    try:
        await injections.inject_once(win.page, "waiting_overlay")
    except Exception:  # noqa: BLE001
        pass
    if await _guard_try_import_cookies(win, base, cs, state):
        log.info("登录成功，Cookie 已注入引擎，返回 APP")
        return True
    log.info("已捕获登录 Cookie 但引擎验证未通过")
    try:
        await win.evaluate_js(injections.get("remove_waiting_overlay"))
    except Exception:  # noqa: BLE001
        pass
    return None


# R-6.72（用户拍板：守护会造成不可控局面，完全抛弃自动干预）：
# 原 _guard_detect_escape / _guard_pull_back_if_logged_out / _guard_hook_forced_nav
# 三件套已整块删除——它们实现「离开登录页 → 自动 load_url 拉回」，与
# framenavigated 事件互为触发源：拉回产生导航事件 → 再判离开 → 再拉回，在页面
# 加载失败（chrome-error://chromewebdata/）时永不收敛，实测打满 CPU 并触发
# CFT Chrome_IOThread CHECK 崩溃。
# 新语义：离开登录页 = 未登录，_guard_monitor_forced 直接判定并结束监视；
# 用户自行再点「去登录」；采集一问一答（未登录立即返回，不等待）。


async def _guard_monitor_forced(win: "ShellWindow", base: str, cs, state) -> bool | None:
    """强制登录单步：True=登录成功 / False=已离开登录页（判定未登录，结束监视）/ None=继续。

    R-6.72（用户拍板：守护会造成不可控局面，完全抛弃）：登录页**不做任何自动
    拉回或自动导航**。离开登录页即视为未登录、立即结束本次监视交回上层，由用户
    自己再点「去登录」。旧逻辑「一离开 /login 就拉回」会在页面加载失败变成
    chrome-error://chromewebdata/ 时自激——拉回产生导航事件→再判离开→再拉回，
    每个循环还附带 3 个 evaluate 全部 TargetClosedError，实测把渲染进程与主进程
    CPU 打满并最终触发 CFT Chrome_IOThread CHECK 崩溃。
    """
    if has_login_cookie(cs):
        return await _guard_handle_id_token(win, base, cs, state)
    try:
        cur = await win.current_url()
    except Exception:  # noqa: BLE001
        cur = ""
    if cur and "/login" not in cur:
        log.info(f"离开登录页（{cur[:70]}），判定未登录并结束监视（不再自动拉回）")
        return False
    return None


def _guard_safe_remove(remove_nav_hook) -> None:
    """移除导航拦截监听（loop 关闭中异常忽略）。"""
    if remove_nav_hook is None:
        return
    try:
        remove_nav_hook()
    except Exception:  # noqa: BLE001（loop 关闭中：忽略）
        pass


async def _guard_read_cookies(win):
    """读当前 Cookie（读取失败按空列表：下一轮再试）。"""
    try:
        return await win.cookies()
    except Exception:  # noqa: BLE001
        return []


async def _guard_monitor_step(win, base: str, cur_force: bool, state: dict) -> bool | None:
    """页内监视单步：返回 True=登录成功 / None=继续监视。"""
    cs = await _guard_read_cookies(win)
    step = _guard_monitor_voluntary if not cur_force else _guard_monitor_forced
    return await step(win, base, cs, state)


def _guard_attach_page_events(page, wake: "asyncio.Event", on_wake) -> None:
    """给页面挂「导航/关闭」监听：状态可能变化时才唤醒守卫（事件源，非轮询）。"""
    wake.clear()
    if page is None or not hasattr(page, "on"):
        return  # 测试替身/页已销毁：无事件源，仅靠进入时的验证
    page.on("framenavigated", lambda *_a: on_wake())
    page.on("close", lambda *_a: on_wake())


async def _guard_verify_burst(win, base: str, cur_force: bool, state: dict) -> bool | None:
    """事件到达后的短验证（最多 3 次）：把一次导航引发的状态收敛掉，不是周期轮询。"""
    for _ in range(3):
        result = await _guard_monitor_step(win, base, cur_force, state)
        if result is not None:
            return result
        await asyncio.sleep(0.6)
    return None


async def _guard_monitor_loop(win: "ShellWindow", base: str, stop: "threading.Event", cur_force: bool) -> bool:
    """小红书页内监视：由页面导航/关闭事件驱动，只在可能变化时读 Cookie。

    R-6.53z8c：取消「每 1 秒读一次 Cookie」的轮询（用户拍板：轮询一律去掉）。
    现在只在 page 触发 framenavigated/close 时唤醒并做一次短验证；无事件期间
    不产生任何平台访问，从根本上减少登录页期间的自动化痕迹。
    """
    state = {"import": 0.0, "inject": 0.0}
    # R-6.72：导航拦截（离开登录页 → 自动 load_url 拉回）整套废弃。
    # 它与 framenavigated 事件互为触发源：拉回产生导航事件 → 再判离开 → 再拉回，
    # 页面加载失败（chrome-error://chromewebdata/）时永不收敛。现在仅保留
    # 「事件到达 → 一次短验证」；离开登录页由 _guard_monitor_forced 直接判未登录结束。
    remove_nav_hook = None
    wake = asyncio.Event()

    def _on_wake(*_a) -> None:
        try:
            wake.set()
        except Exception:  # noqa: BLE001（事件循环关闭中：忽略）
            pass

    _guard_attach_page_events(win.page, wake, _on_wake)
    try:
        # 进入即验证一次（守卫可能在已登录态被拉起）
        result = await _guard_verify_burst(win, base, cur_force, state)
        if result is not None:
            return result
        while not stop.is_set():
            try:
                # 超时仅用于让 stop 及时生效：不读 Cookie、不判登录，不构成业务轮询
                await asyncio.wait_for(wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            wake.clear()
            if await _rebind_if_closed(win, base):
                _guard_attach_page_events(win.page, wake, _on_wake)  # 重绑后事件源换成新页
                continue
            result = await _guard_verify_burst(win, base, cur_force, state)
            if result is not None:
                return result
        return False
    finally:
        _guard_safe_remove(remove_nav_hook)


async def _guard_startup_status(base: str) -> None:
    """启动时按 login_status 判定三态。R-6.48p（用户最新拍板，覆盖 R-6.48i）：
    启动未登录/被踢**不再自动强制导航登录页**——壳窗保持 APP 界面，前端
    ForceLogin 登录提示框自动弹出（App.vue onMounted 检测），用户点「去登录」
    才经 login_begin(pending)→守卫导航小红书登录页。已登录启动不受影响
    （守卫待命，仅响应登录引导 pending）。"""
    ls = await _guard_get_json(base, "/api/login_status")
    kicked = bool(ls and ls.get("kicked"))
    if ls and ls.get("logged_in") and not kicked:
        log.info("启动已登录——守卫待命（仅响应登录引导 pending）")
        return
    reason = "启动检测到登录已被平台踢出" if kicked else "启动未登录"
    log.info(
        f"{reason}——壳窗保持 APP 界面，"
        "等待前端登录提示框「去登录」触发导航（不再自动跳转登录页）",
    )


async def _guard_next_force(gate, win, base, stop) -> bool | None:
    """等下一次「去登录」触发——只有事件源，没有轮询退化路径。

    R-6.48q：pending 事件驱动，login_begin 置位 evt 才醒来。
    R-6.53z8h：删掉 gate 缺失时的 1.5s 轮询 + HTTP 查询兜底。生产调用点
    （launcher）100% 注入 gate；没有事件源就没有登录信号，此时阻塞到 stop
    为止即可，不允许再用周期查询顶着。
    """
    if gate is None or gate.get("evt") is None:
        await asyncio.to_thread(stop.wait)  # 无事件源：阻塞到停止，零轮询
        return None
    await gate["evt"].wait()
    gate["evt"].clear()
    if not gate.get("pending"):
        return None
    return bool(gate.get("force"))


async def _guard_login_canvas(win: "ShellWindow") -> "ShellWindow":
    """登录落点：右侧调试窗（采集窗）。左窗只显示 APP，全程不参与登录。

    R-6.53z8d：这是双窗口职责分离的入口——登录页只出现在右窗，壳窗永不被导航。
    右窗缺失时现场开一个（登录必须有头有窗，headless 会被平台风控拦死）。
    """
    coord = get_coordinator()
    p = coord.collect_win()
    if p is not None and not p.is_closed():
        return ShellWindow(p, win.base_url)
    try:
        from src import engine_pool  # noqa: PLC0415

        eng = await engine_pool.pool.get("default")
        ctx = await eng.context()
        p = await ctx.new_page()
        coord.register_collect_win(p)
        log.info("右侧调试窗缺失，已新开一个作为登录落点")
        return ShellWindow(p, win.base_url)
    except Exception as e:  # noqa: BLE001（极端场景：引擎未就绪，退回壳窗保底可用）
        log.warning(f"新开登录窗失败，回退壳窗: {e!r}")
        return win


async def _guard_announce_and_navigate(win, base, force) -> None:
    """R-6.37i 拆自 login_guard：打印切换日志并导航到登录页（R-6.21e 只响应 pending）。"""
    log.info(
        "切换到小红书登录页"
        f"（{'强制登录·不可关闭' if force else '主动重登·可关闭'}）",
    )
    await _guard_navigate_to_login(win, base, force)


@xlog.tagged("win:collect:login")
async def login_guard(win: ShellWindow, base_url: str, stop: "threading.Event", gate: dict | None = None) -> None:
    """登录守卫（R-6.5 真实登录页方案；R-6.6 强制登录；R-6.9 主动重登；R-6.10 首页模式）。

    语义（用户拍板）：
    - 启动未登录/被踢：R-6.48p 起壳窗保持 APP 界面，前端 ForceLogin 提示框弹出，
      用户点「去登录」才导航登录页（不再自动导航）；已登录启动守卫待命。
    - 运行时被踢不直接导航（前端弹强制登录框，用户点击触发）。
    - 主动重登可关闭（✕ 回 APP）；强制登录不可关闭（force_login_js 锁死 + URL 拉回）。
    - 登录成功：id_token → 等待遮罩 → import_cookies 注入引擎 → 回 APP。
    - R-6.48q：pending 等待改为**事件驱动**——launcher 注入同进程 `_LOGIN_GATE`
      （含 asyncio.Event），用户点「去登录」→ login_begin 置位 evt → 守卫 `await
      evt.wait()` 醒来导航，主循环零轮询。gate 为 None（测试/无壳直跑）退化为
      1.5s 低频轮询，语义等价。
    CZ_SKIP_LOGIN_GUARD=1 可跳过（自动化路径）。R-6.36e：循环块拆至上方助手，语义逐字保留。
    """
    if os.environ.get("CZ_SKIP_LOGIN_GUARD"):
        return

    base = base_url.rstrip("/")
    await _guard_startup_status(base)

    in_login = False
    while not stop.is_set():
        if stop.is_set():
            return
        force = await _guard_next_force(gate, win, base, stop)
        if force is None:
            continue  # 事件已消费但 pending 被清：不导航，回挂起
        # R-6.19：Tab1 被关闭后 keeper 即时重建——守卫重绑定新壳 page，重建即重置登录状态
        if await _rebind_if_closed(win, base):
            in_login = False
            continue
        if in_login:
            continue  # 正在登录页（局部轮询控制）
        login_win = await _guard_login_canvas(win)
        await _guard_announce_and_navigate(login_win, base, force)
        in_login = True
        ok = await _guard_monitor_loop(login_win, base, stop, force)
        in_login = False
        if ok:
            # R-6.53z8d：壳窗全程未被导航，无需「回 APP」；登录页会自行跳转浏览页
            log.info("登录完成——左窗保持 APP，右窗由平台自行跳转浏览页")

async def _coord_or_scan_shell_page(base: str):
    """R-6.37i 拆自 _rebind_if_closed：协调器壳引用；未就绪则回退 URL 扫描。"""
    from src import engine_pool  # noqa: PLC0415

    coord = get_coordinator()
    p = coord.shell()
    if p is None:
        eng = await engine_pool.pool.get("default")
        ctx = await eng.context()
        for q in ctx.pages:
            if not q.is_closed() and (q.url or "").startswith(base):
                p = q
                break
    return p


@xlog.tagged("win:collect:login")
async def _rebind_if_closed(win: "ShellWindow", base: str) -> bool:
    """R-6.19：壳 tab 被 keeper 重建后，旧 page 已关闭——登录守卫重绑定新壳 page。

    R-6.30c：优先取协调器判活引用（keeper 登记的重建壳窗）；协调器未就绪
    （keeper 尚未启动的极早期）才回退 URL 扫描 ctx.pages。
    """
    try:
        if win.page.is_closed():
            from src import engine_pool  # noqa: PLC0415

            p = await _coord_or_scan_shell_page(base)
            if p is not None:
                win.rebind(p)
                # R-6.22：重绑定目标若是重建的新窗口，校准为左半屏布局
                win_w, win_h, _, _ = engine_pool.shell_window_geometry()
                await _calibrate_window(p, 0, 0, win_w, win_h)
                # R-6.48i：重绑定目标补标题守护——裸窗口 document 无注入即
                # 用户实测「登录页 title 变小红书 / 看到标题变化过程」的根因路径。
                await _ensure_shell_title(p)
                # R-6.48k：重绑定目标补登录页行为注入（弹窗锁/居中，init script 已
                # 注册时仅 evaluate 当前文档）——事件点补注，非轮询。
                await _ensure_login_injects(win)
                log.info("login_guard 已重绑定重建的壳 tab")
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------- keep_shell_tab 拆分助手（R-6.36e：嵌套闭包提升为模块级，控制流逐字等价） ----------

def _keeper_shell_effective(coord, ctx):
    """壳窗引用判活（R-6.48o）：is_closed() 在引擎崩溃/CDP 断连时不可靠
    （page 对象不标记 closed），必须同时校验引用仍属于当前页面集——
    不在 ctx.pages = 会话已重建（崩溃恢复），返回 None。"""
    s = coord.shell()
    if s is None or s.is_closed() or s not in list(ctx.pages):
        return None
    return s


async def _keeper_find_shell(ctx, base, coord):
    """协调器引用判活优先——壳窗可能正被守卫导航到登录页（URL 不含 base），URL 匹配会误判「壳窗被关」。

    R-6.48h：URL 扫描必须排除 login-hint（同为 8791 前端子路由，startswith(base)
    会命中）——否则调试窗口会被误登记为壳窗，**关闭调试窗口触发「壳窗被关闭→
    应用退出」**（用户实测：关调试窗口整个应用退出，日志实锤
    '壳窗 close 事件（url=.../login-hint）→ 触发应用退出'）。
    """
    alive = _keeper_shell_effective(coord, ctx)
    if alive is not None:
        return alive
    for p in ctx.pages:
        u = p.url or ""
        if not p.is_closed() and u.startswith(base):
            coord.register_shell(p)
            return p
    return None


@xlog.tagged("win:collect:find")
def _match_collect_page(p, shell, coord):
    """R-6.37g 拆自 _keeper_find_collect_win：单页是否 采集窗（explore 或 login-hint）。"""
    if p is shell or p.is_closed():
        return None
    u = p.url or ""
    if "xiaohongshu.com" in u:
        log.info(f"_find_collect_win 命中: url={u[:60]!r}")
        coord.register_collect_win(p)
        return p
    return None


async def _keeper_find_collect_win(ctx, shell, coord):
    """采集窗（采集窗）可能在 explore 或 /login-hint，两者都算任务 tab。"""
    alive = coord.collect_win()
    if alive is not None and alive is not shell:
        return alive
    for p in ctx.pages:
        t2 = _match_collect_page(p, shell, coord)
        if t2 is not None:
            return t2
    return None


async def _keeper_rebuild_shell(ctx, base, coord, engine_pool):
    """应用模式重建壳窗（--app 路由）；路由不可用退回普通 tab。"""
    from src.engine_types import builtin_cft_candidates  # noqa: PLC0415

    win_w, win_h, _, _ = engine_pool.shell_window_geometry()
    eng = await engine_pool.pool.get("default")
    cfts = builtin_cft_candidates()
    page = await _launch_app_window(
        ctx, base,
        user_data_dir=eng.user_data_dir,
        executable=cfts if cfts else None,
        left=0, width=win_w, height=win_h,
    )
    if page is None:
        page = await ctx.new_page()
        try:
            await page.goto(base, wait_until="domcontentloaded", timeout=8000)
        except Exception as e:  # noqa: BLE001（goto 失败：关闭空页，交由外层/兜底处理）
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
            raise
    try:
        await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                       label="shell.bring_to_front", skip=True)
    except Exception:  # noqa: BLE001
        pass
    await _calibrate_window(page, 0, 0, win_w, win_h)
    await _ensure_shell_title(page)
    coord.register_shell(page)
    coord.watch(page, is_shell=True)
    log.info("壳窗口初始创建完成（R-6.22p 关主窗=退出应用）")


_LOGIN_PAGE_URL = "https://www.xiaohongshu.com/login"


async def _keeper_collect_target_url(config, engine_pool):
    """右窗目标 URL：一律小红书登录页。

    R-6.53z8k 用户判据：能走 URL 的小红书页只有 login 这一个；登录成功后 login 页
    自己会跳 explore，我方不做跳转。故右窗恒进 login——未登录时它就是扫码入口，
    已登录由平台自跳，无需中间页。

    取消本地 /login-hint 中间页：它让用户多点一次；页内「开始采集」用
    location.href 进 explore，本身属于用 URL 跳非 login 页。
    """
    return _LOGIN_PAGE_URL


async def _keeper_sweep_blank(ctx, shell) -> int:
    """清理空白/残留 tab（壳窗引用跳过）。

    R-6.48o：扩展 chrome://newtab 清理——引擎崩溃后 Chrome 会话恢复会把崩溃前
    的标签全部还原，新上下文首个默认页 chrome://newtab 也在此列；此前只清
    ""/about:blank 导致崩溃恢复后残留空标签（用户实测：崩溃重启后多出空白窗口）。
    R-6.53t：返回关闭数并打日志（用户手动新标签 5s 内被关=「自动化操作」取证）。
    R-6.53z8j：原先靠 _KEEPER_SWEEP_LAST 把调用降频到 30s，但 5s 轮询删掉后
    本函数只在启动时调一次，节流变量从未被读取（死代码），一并删除。
    """
    closed = 0
    # R-6.53z8：/login 是登录窗扫码入口，keeper 一律不主动关——平台登录成功会
    # 自己跳走；cookie 存在不代表平台已登录（实测停在 /login 不跳），按 cookie
    # 关闭会误关扫码入口。这里只清空白/新标签页。
    for p in list(ctx.pages):
        if p is shell or p.is_closed():
            continue
        if await _close_if_blank(p, (p.url or "").strip()):
            closed += 1
    return closed


_BLANK_URLS = ("", "about:blank", "chrome://newtab", "chrome://newtab/")


@xlog.tagged("win:collect:sweep")
async def _close_if_blank(p, u: str) -> bool:
    """空白/新标签页才关闭（返回 True=已关闭）；/login 等正常页面一律保留。"""
    if u not in _BLANK_URLS:
        return False
    await _safe_close(p)
    log.info(f"清理空白/新标签页 url={u!r}")
    return True


async def _keeper_recovery_cleanup(ctx, shell) -> int:
    """崩溃恢复清理：壳窗在监护期间失效后重新找到 → 关闭所有非主窗口残留。

    R-6.48o：引擎崩溃 → Chrome 会话恢复还原全部标签（调试窗 采集窗、残留页）。
    用户拍板「崩溃后要关闭所有相关窗口，重启时关闭之前未关闭的窗口」——
    恢复后只保留主窗口（登录壳），其余全部关闭。返回关闭数。
    """
    closed = 0
    for p in list(ctx.pages):
        if p is shell or p.is_closed():
            continue
        await _safe_close(p)
        closed += 1
    return closed


async def _safe_close(p) -> None:
    try:
        await p.close()
    except Exception:  # noqa: BLE001
        pass


def _keeper_log_snapshot(ctx, coord, last):
    """页面集变化才打印快照（防日志刷屏）；返回更新后的 _last_urls。"""
    _urls = frozenset((p.url or "") for p in ctx.pages if not p.is_closed())
    if _urls != last[0]:
        last[0] = _urls
        _sh = coord.shell()
        log.info(
            "keeper 页面集="
            + " | ".join((p.url or "")[:45] for p in ctx.pages if not p.is_closed())
            + "  shell_ref=" + ((_sh.url or "")[:30] if _sh else "None"),
        )
    return last[0]


# R-6.53z8k+ 扫码路径：右窗恒开登录页后，用户直接扫码、不再点「去登录」，
# 守卫的 _LOGIN_GATE pending 事件永不触发 → 登录成功的 Cookie 没人导入 →
# 后端命中「失效记忆」一直判未登录。这里挂采集窗导航事件补上这条路径
# （事件驱动，30s 节流；只在页面已离开 /login 且持有 web_session 时触发）。
_LOGIN_PROBE_LAST = [0.0]
_LOGIN_PROBE_GAP = 30.0


def _watch_collect_win_login(page) -> None:
    """给采集窗挂导航监听：扫码成功后自动导入 Cookie（同一页只挂一次）。"""
    if page is None or not hasattr(page, "on"):
        return  # 测试替身 / 页已销毁
    if getattr(page, "_xhs_login_probe", False):
        return
    try:
        setattr(page, "_xhs_login_probe", True)
        setattr(page, "_xhs_login_from", _safe_url(page))
        page.on("framenavigated",
                lambda *_a: asyncio.create_task(_probe_collect_win_login(page)))
    except Exception as e:  # noqa: BLE001
        log.warning(f"采集窗登录监听挂载失败: {e!r}")


def _safe_url(page) -> str:
    """读页面当前地址（页已销毁/被关时返回空串）。"""
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001
        return ""


async def _probe_collect_win_login(page) -> None:
    """页面离开 /login 且持有 web_session → 把 Cookie 导入引擎（幂等，30s 节流）。

    只有"上一次看到的地址还在 /login"才算真扫码（用户必须先停在登录页扫码，
    页面才会从 /login 跳到浏览页）；启动/重建时页面本来就在浏览态，那种导入是
    恢复既有登录态，不能打「扫码成功」——否则排障时会被这句假话带偏。
    """
    now = time.monotonic()
    if now - _LOGIN_PROBE_LAST[0] < _LOGIN_PROBE_GAP:
        return
    _LOGIN_PROBE_LAST[0] = now
    was_login = "/login" in (getattr(page, "_xhs_login_from", "") or "")
    setattr(page, "_xhs_login_from", _safe_url(page))
    xhs = await _login_cookies_on_browsing_page(page)
    if not xhs:
        return
    await _post_import_cookies(xhs, fresh_scan=was_login)


async def _login_cookies_on_browsing_page(page) -> list:
    """页面已离开 /login 时，返回带 web_session 的小红书 Cookie；否则空列表。"""
    if not await _is_browsing_page(page):
        return []
    xhs = await _xhs_cookies(page)
    if not any(c.get("name") == "web_session" for c in xhs):
        return []
    return xhs


async def _is_browsing_page(page) -> bool:
    """当前页是否小红书浏览页（已离开 /login）。"""
    try:
        u = page.url or ""
    except Exception:  # noqa: BLE001
        return False
    return "xiaohongshu.com" in u and "/login" not in u


async def _xhs_cookies(page) -> list:
    """读当前上下文的小红书域 Cookie（读取失败返回空）。"""
    try:
        cs = await page.context.cookies()
    except Exception:  # noqa: BLE001
        return []
    return [c for c in cs if "xiaohongshu" in (c.get("domain") or "")]


async def _post_import_cookies(cookies, fresh_scan: bool = False) -> None:
    """POST /api/auth/import_cookies（触发登录成功链路：解除失效记忆/唤醒续采/推事件）。

    fresh_scan=True 表示这次是真的从登录页扫码跳过来的；False 是启动/重建时
    恢复既有登录态的例行同步。
    """
    from src import config  # noqa: PLC0415

    base = f"http://127.0.0.1:{config.front_port()}"
    body = json.dumps({"cookies": cookies}).encode()
    req = urllib.request.Request(
        f"{base}/api/auth/import_cookies", data=body,
        headers={"Content-Type": "application/json"})

    def _do() -> bytes:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    try:
        await asyncio.to_thread(_do)
        if fresh_scan:
            log.info("扫码成功——Cookie 已自动导入引擎（未点「去登录」路径）")
        else:
            log.info("登录态恢复——引擎 Cookie 已同步（非本次扫码）")
    except Exception as e:  # noqa: BLE001
        log.warning(f"扫码自动导入 Cookie 失败: {e!r}")


@xlog.tagged("win:collect:calibrate")
async def _calibrate_reused_collect_win(t2, _t2_url, coord, engine_pool) -> None:
    """R-6.37i 拆自 _keeper_setup_collect_win：复用残留 采集窗 时校准窗口并注册监听。

    R-6.49：采集页（xiaohongshu.com）保持不动（合法采集目标，禁止启动路径导航走）；
    其余页按目标（未登录 login-hint / 已登录 APP 首页）校准。"""
    try:
        if _t2_url and "xiaohongshu.com" not in (t2.url or ""):
            await t2.goto(_t2_url, wait_until="domcontentloaded", timeout=10000)
        # R-6.48h：复用残留路径同样补标题守护（此前仅新建路径注入——重启保留的
        # 采集窗 裸奔显示 login-hint 原始标题「尚未登录小红书」）
        await _ensure_debug_title(t2)
        win_w2, win_h2, _, win_left2 = engine_pool.shell_window_geometry()
        await _calibrate_window(t2, win_left2, 0, win_w2, win_h2)
        coord.register_collect_win(t2)
        coord.watch(t2, is_shell=False)
        _watch_collect_win_login(getattr(t2, "page", t2))
    except Exception as e:  # noqa: BLE001
        log.warning(f"采集窗 采集窗口复用校准失败: {e!r}")


@xlog.tagged("win:collect:setup")
async def _keeper_setup_collect_win(ctx, eng, coord, engine_pool, config):
    """初始块 采集窗：复用残留 explore/login-hint 或新建采集窗（失败延迟重试 1 次）。"""
    async with engine_pool.COLLECT_WIN_GUARD_LOCK:
        _t2_url = await _keeper_collect_target_url(config, engine_pool)
        t2 = await _keeper_find_collect_win(ctx, coord.shell(), coord)
        if t2 is not None:
            await _calibrate_reused_collect_win(t2, _t2_url, coord, engine_pool)
            return
        for _attempt in range(2):
            try:
                # R-6.49c：启动路径 采集窗 走 open_collect_window 应用模式（--app 无地址栏/标签条），
                # 与采集路径形态一致——不再 new_page 建普通 tab（带地址栏，用户反馈
                # 「采集调试窗口变了、出现地址栏、不是 app 形态」）。
                from src.engine_types import builtin_cft_candidates  # noqa: PLC0415
                _cfts = builtin_cft_candidates()
                # R-6.53p：builtin_cft_candidates() 返回 str（单个路径）而非列表——
                # 旧写法 _cfts[0] 取到首字符 '/'，Popen 用 '/' 当引擎路径 →
                # PermissionError(13) → 初始 采集窗（login-hint 窗）创建 3 次全失败。
                _exec = _cfts if _cfts else None
                page = await open_collect_window(
                    ctx,
                    coord.shell(),
                    url=_t2_url,
                    user_data_dir=eng.user_data_dir,
                    executable=_exec,
                )
                # open_collect_window 已内建 goto/login-hint、标题守护、窗口校准
                coord.register_collect_win(page)
                coord.watch(page, is_shell=False)
                _watch_collect_win_login(page)
                return
            except Exception as e:  # noqa: BLE001
                if _attempt == 0:
                    log.warning(f"采集窗 采集窗口首次创建失败（延迟重试）: {e!r}")
                    await asyncio.sleep(4.0)
                else:
                    log.warning(f"采集窗 采集窗口初始创建失败（可稍后经设置开关重开）: {e!r}")


@xlog.tagged("win:shell")
@xlog.tagged("win:collect:sweep")
async def _keeper_dedupe_xhs_pages(ctx, coord) -> None:
    """启动期去重：强杀后 Chrome 会话恢复 + 应用窗口新建 会各留一个采集页。

    R-6.53z8k+：右窗改恒开登录页后，重启实测右窗出现两个 explore 标签（用户红线
    「禁止多窗口残留」）。只关 URL 完全相同、且不是 coord 已注册那页的多余副本。
    """
    keep = getattr(coord.collect_win(), "page", coord.collect_win())
    dups = _duplicate_xhs_pages(ctx, keep)
    n_xhs = len([p for p in ctx.pages if "xiaohongshu.com" in (getattr(p, "url", "") or "")])
    log.info(f"采集页去重检查：小红书页 {n_xhs} 个，重复待关 {len(dups)} 个")
    for p in dups:
        try:
            await p.close()
            log.info(f"已关闭重复采集页（右窗只留一个）: {_page_key(p)[:50]}")
        except Exception as e:  # noqa: BLE001
            log.warning(f"关闭重复采集页失败: {e!r}")


def _duplicate_xhs_pages(ctx, keep) -> list:
    """同 URL 的小红书页只留第一份（coord 已注册的 keep 优先保留）。"""
    seen = {_page_key(keep)} - {""}
    dups = []
    for p in ctx.pages:
        u = _page_key(p)
        if p is keep or not u:
            continue
        if u in seen:
            dups.append(p)
            continue
        seen.add(u)
    return dups


def _page_key(page) -> str:
    """页 URL 归一（去 hash / 尾斜杠）；非小红书页返回空串。"""
    u = getattr(page, "url", "") or ""
    if "xiaohongshu.com" not in u:
        return ""
    return u.split("#")[0].rstrip("/")


async def _keeper_init_pass(base, coord, config, engine_pool, last):
    """初始块：确保 Tab1 存在挂监听；采集窗 按开关初始创建一次；清理空白残留。"""
    eng = await engine_pool.pool.get("default")
    ctx = await eng.context()
    _keeper_log_snapshot(ctx, coord, last)
    shell = await _keeper_find_shell(ctx, base, coord)
    if shell is None:
        await _keeper_rebuild_shell(ctx, base, coord, engine_pool)
    else:
        coord.register_shell(shell)
        coord.watch(shell, is_shell=True)
    await _keeper_sweep_blank(ctx, coord.shell())
    if config.get_config().get("collect_tab_visible"):
        await _keeper_setup_collect_win(ctx, eng, coord, engine_pool, config)
    await _keeper_sweep_blank(ctx, coord.shell())
    await _keeper_dedupe_xhs_pages(ctx, coord)
    # 会话恢复出来的页可能晚于 init 出现：2.5s 后再查一次（一次性延迟，非轮询）
    asyncio.get_running_loop().call_later(
        2.5, lambda: asyncio.ensure_future(_keeper_dedupe_xhs_pages(ctx, coord)))



def _server_has_running_collect() -> bool:
    """R-6.53o：采集执行期间 keeper 暂停 collect_win 治理/崩溃清理，避免与采集器 CDP
    并发触发引擎假死、且崩溃恢复清理误关采集中的搜索页。延迟导入规避循环依赖。"""
    try:
        from src import server as _server  # noqa: PLC0415（运行时 server 已加载，取任务注册表）
        return any(st.get("status") == "running" for st in _server._COLLECT_TASKS.values())
    except Exception:  # noqa: BLE001（状态源不可用视为不采集，keeper 正常治理）
        return False


@xlog.tagged("win:shell")
async def keep_shell_tab(base_url: str, stop: "threading.Event", on_shell_closed: "Callable[[], None] | None" = None) -> None:
    """R-6.22p 窗口监护：关主窗=关闭应用（on_shell_closed 退出）；关采集窗=仅关采集窗不复活。

    初始块确保 Tab1/采集窗 创建一次；兜底轮询 5s 检测壳窗缺失。R-6.36e：
    嵌套闭包提升为上方 _keeper_* 模块级助手，控制流与语义逐字保留。
    """
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    base = base_url.rstrip("/")
    coord = get_coordinator()
    coord.bind(stop, on_shell_closed)
    coord.attach_engine(engine_pool.pool)  # R-6.53h：传 EnginePool 单例实例（此前误传模块导致 _shell_closed_verify AttributeError）
    last = [frozenset()]  # R-6.28：页面集变化才打印快照

    # 初始：确保 Tab1 存在并挂监听；采集窗 按开关初始创建一次
    while not stop.is_set():
        try:
            await _keeper_init_pass(base, coord, config, engine_pool, last)
            break
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                log.warning(f"Tab1 初始化监护暂不可用（重试）: {e!r}")
            await asyncio.sleep(2.0)

    # R-6.53z8d：取消 5s 兜底轮询。「壳窗被关=退出应用」由 close 事件驱动
    # （_handle_close → 3s 校验 → _shell_closed_verify）；右窗被关由 close 事件
    # 按需重建（_schedule_collect_win_rebuild）。此处只阻塞等待停止信号，零周期查询。
    await asyncio.to_thread(stop.wait)