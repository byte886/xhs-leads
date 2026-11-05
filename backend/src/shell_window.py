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


async def open_tab2_window(
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
# 登录成功后小红书自动跳转 redirectPath（首页），守卫捕获 web_session 注入引擎后回 APP。
XHS_LOGIN_URL = (
    "https://www.xiaohongshu.com/login"
    "?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F"
)

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
# R-6.30c 轻量窗口协调器：shell/tab2 page 引用共享中心
# ---------------------------------------------------------------------------
class WindowCoordinator:
    """keeper / guard / engine_pool 共用的窗口引用中心（轻量，不引入事件总线/状态机）。

    - keeper（keep_shell_tab）是引用的**写入方**：发现/重建/创建 shell 与 tab2 时
      register_* 登记，并 watch() 幂等挂载 close 回调；
    - guard（login_guard）与 engine_pool.cleanup_tabs 是**读取方**：shell()/tab2()
      判活取引用，不再各自遍历 ctx.pages 找窗口（窗口竞态/震荡的历史根源）；
    - 引用失效：page 关闭后判活返回 None，由 keeper 按需重建/重新登记；
    - 命令式方法：navigate_tab2 / show_login_hint / close_orphan_explore。
    """

    def __init__(self):
        self._shell = None   # 壳窗 page 引用（登录导航 URL 变化不影响判活）
        self._tab2 = None    # 采集窗（Tab2）page 引用
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

    async def _shell_closed_verify(self) -> None:
        """R-6.53c：壳窗 close 事件延迟 3s 校验——引擎崩溃时壳窗 close 是引擎
        进程死亡的副作用（非用户行为），跳过退出交给 keeper 重建；引擎存活且
        非重建期才是用户真关主窗，维持「关主窗=退出应用」语义。"""
        if self._stop is not None and self._stop.is_set():
            return
        eng = self._engine
        account = None
        if eng is not None:
            try:
                # attach_engine 传入 EnginePool 池对象 → 取账号引擎实例
                account = await eng.get("default") if hasattr(eng, "get") else eng
            except Exception:  # noqa: BLE001（引擎未就绪：按存活处理走退出兜底）
                account = None
            if account is not None:
                try:
                    # R-6.53h：对象类型防御——校验异常时保守不退出，交给 keeper 轮询兜底，
                    # 避免「崩溃恢复被误判用户关窗退出应用」（历史：误传模块导致 AttributeError）
                    if account.rebuild_age() < 60:
                        print("[xhs-leads][keeper] 壳窗 close 校验：引擎 60s 内重建过（崩溃恢复），跳过退出", flush=True)
                        return
                    if not account.is_connected_now():
                        print("[xhs-leads][keeper] 壳窗 close 校验：引擎连接已断开（崩溃），跳过退出", flush=True)
                        return
                except Exception:  # noqa: BLE001（对象无校验方法/异常：无法确认引擎状态，保守不退出）
                    print("[xhs-leads][keeper] 壳窗 close 校验：引擎状态校验异常，保守跳过退出", flush=True)
                    return
        if self._on_shell_closed is not None:
            print("[xhs-leads][keeper] 壳窗被关闭（引擎存活），触发应用退出", flush=True)
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

    def tab2(self):
        """采集窗引用（判活）；未登记/已关闭返回 None。"""
        return self._alive(self._tab2)

    def register_shell(self, page) -> None:
        self._shell = page

    def register_tab2(self, page) -> None:
        self._tab2 = page

    # ---------------- close 监听（幂等挂载） ----------------
    def watch(self, page, is_shell: bool) -> None:
        if any(p is page for p in self._watched):
            return
        self._watched.add(page)

        def _on_close() -> None:
            try:
                if self._stop is not None and self._stop.is_set():
                    return
                if is_shell:
                    # R-6.53c：引擎崩溃时壳窗 close 事件随引擎进程死亡触发，不能
                    # 立即退出——延迟 3s 校验引擎状态，区分「用户关主窗」与「崩溃」。
                    self._shell_close_ts = time.monotonic()  # R-6.53f：供轮询兜底
                    print("[xhs-leads][keeper] 壳窗 close 事件，3s 后校验（区分用户关闭/引擎崩溃）", flush=True)
                    try:
                        # R-6.53g：close 回调在 playwright 驱动线程，必须用 bind 时
                        # 捕获的 keeper loop 调度；无 loop 时仅靠轮询 shell_close_age 兜底。
                        if self._loop is not None:
                            self._loop.call_later(3.0, lambda: asyncio.ensure_future(
                                self._shell_closed_verify()))
                    except Exception:  # noqa: BLE001（loop 关闭中：keeper 轮询兜底）
                        pass
                else:
                    print("[xhs-leads][tab2] 采集窗口被关闭（仅关闭采集窗，应用保持运行）", flush=True)
            except Exception:  # noqa: BLE001（loop 关闭中：兜底轮询接管）
                pass

        def _on_crash() -> None:
            """R-6.48o：壳窗口渲染器崩溃（Chrome 弹崩溃对话框→会话恢复，
            App 不退出）→ 延迟恢复清理：关闭其余窗口，崩溃页留给 Chrome 恢复。"""
            try:
                if self._stop is not None and self._stop.is_set():
                    return
                if is_shell:
                    print("[xhs-leads][keeper] 壳窗口崩溃，触发残留窗口清理", flush=True)
                    try:
                        loop = asyncio.get_event_loop()
                        loop.call_later(3.0, lambda: asyncio.ensure_future(
                            self._recovery_cleanup_safe(page)))
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        try:
            # R-6.22j 取证：记录壳窗 close 时的 URL，定位「壳窗被关」来源
            page.on("close", lambda: print(
                f"[xhs-leads][keeper] 壳窗 close 事件（url={page.url!r}）", flush=True))
            page.on("close", _on_close)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.on("crash", _on_crash)
        except Exception:  # noqa: BLE001（事件不支持时静默：真实场景仍由 close/轮询兜底）
            pass

    async def _recovery_cleanup_safe(self, page):
        """壳窗口崩溃后的恢复清理（延迟执行）：等 Chrome 会话恢复稳定后，
        关闭非崩溃页的全部窗口，崩溃页本身保留由 Chrome 恢复。"""
        try:
            await asyncio.sleep(3.0)
            ctx = page.context
            n = await _keeper_recovery_cleanup(ctx, page)
            if n:
                print(f"[xhs-leads][keeper] 崩溃恢复：关闭 {n} 个残留窗口，仅保留主窗口", flush=True)
        except Exception:  # noqa: BLE001（引擎已死/重连中：兜底轮询接管）
            pass

    # ---------------- 命令式方法 ----------------
    async def navigate_tab2(self, url: str, timeout: int = 8000) -> None:
        """Tab2 导航到指定 URL（引用失效时静默跳过——keeper 下轮重建后再管）。"""
        t2 = self.tab2()
        if t2 is None:
            return
        await t2.goto(url, wait_until="domcontentloaded", timeout=timeout)

    async def show_login_hint(self, front_port: int) -> None:
        """Tab2 显示未登录提示页（R-6.28 未登录闭环）。"""
        await self.navigate_tab2(f"http://127.0.0.1:{front_port}/login-hint")

def _is_orphan_explore_url(u: str) -> bool:
    """R-6.37i：URL 是否孤儿 explore 页（小红书域且非 /login）。"""
    return "xiaohongshu.com" in u and "/login" not in u


async def _close_if_orphan_explore(p, shell, t2) -> None:
    """R-6.37i 拆自 close_orphan_explore：单页若为孤儿 explore 则关闭（壳/Tab2 保留）。"""
    if p is shell or p is t2 or p.is_closed():
        return
    if _is_orphan_explore_url(p.url or ""):
        try:
            await p.close()
        except Exception:  # noqa: BLE001
            pass


    async def close_orphan_explore(self, ctx) -> None:
        """未登录时关闭孤儿 explore 判定页（login_status 轮询可能新开）；
        壳窗与 Tab2 引用无条件保留。"""
        shell, t2 = self.shell(), self.tab2()
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
                print(
                    f"[xhs-leads][shell] --app 窗口路由失败（第{attempt + 1}/3 次）: {e!r}\n"
                    f"  cwd={os.getcwd()} executable={executable!r}\n"
                    f"  user_data_dir={user_data_dir!r} url={url!r}\n"
                    f"  {''.join(_tb.format_exception_only(type(e), e)).strip()}",
                    flush=True,
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
    print(
        f"[xhs-leads][shell] 窗口校准失败（{left},{top} {width}x{height}，"
        f"重试 3 次仍失败）: {last_err!r}",
        flush=True,
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
    print(f"[xhs-leads][shell] 壳 tab 导航前端未成功（最后错误: {last_err!r}）", flush=True)


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

    if config.get_config().get("headless"):
        config.set_config("headless", False)  # 壳必须有头（A 路径常态）
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
                print(f"[xhs-leads][guard] {key} init script 已注册", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[xhs-leads][guard] {key} add_init_script 失败: {e!r} url={win.page.url[:60]!r}", flush=True)
        try:
            await win.page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads][guard] {key} evaluate 失败: {e!r} url={win.page.url[:60]!r}", flush=True)


async def _guard_inject_active_close(win: "ShellWindow", base: str) -> None:
    try:
        await win.evaluate_js(injections.get("active_close").replace("__BASE__", f"{base}/"))
    except Exception as e:  # noqa: BLE001
        print(f"[xhs-leads][guard] 主动重登关闭按钮注入暂未成功: {e!r}", flush=True)


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
            print(f"[xhs-leads][guard] 强制登录前清除壳窗口小红书 Cookie {n} 条", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads][guard] 清除壳窗口 Cookie 异常: {e!r}", flush=True)
    try:
        await win.load_url(XHS_LOGIN_URL)
        print("[xhs-leads][guard] load_url 已调用（无异常）", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[xhs-leads][guard] load_url 异常: {e!r}", flush=True)
    try:
        await win.evaluate_js(f"window.location.href = '{XHS_LOGIN_URL}'")
        print("[xhs-leads][guard] evaluate_js 导航已下发（双保险）", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[xhs-leads][guard] evaluate_js 异常: {e!r}", flush=True)
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


async def _guard_try_import_cookies(win: "ShellWindow", base: str, cs, state) -> bool:
    """id_token 出现且满 0.5s → 注入引擎；成功返回 True。state 持 _t_last_import。"""
    if not has_id_token(cs):
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
        print("[xhs-leads][guard] 主动重登成功，Cookie 已注入引擎，返回 APP", flush=True)
        return True
    try:
        cur = await win.current_url()
    except Exception:  # noqa: BLE001
        cur = ""
    if cur.startswith(base):
        print("[xhs-leads][guard] 主动重登已关闭（回 APP，登录态不变）", flush=True)
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
        print("[xhs-leads][guard] 登录成功，Cookie 已注入引擎，返回 APP", flush=True)
        return True
    print("[xhs-leads][guard] 已捕获登录 Cookie 但引擎验证未通过", flush=True)
    try:
        await win.evaluate_js(injections.get("remove_waiting_overlay"))
    except Exception:  # noqa: BLE001
        pass
    return None


def _guard_hook_forced_nav(win: "ShellWindow", stop: "threading.Event"):
    """R-6.48h 强制登录导航拦截：主窗口主 frame 一旦离开 /login（小红书域内
    乱点/外链跳转），事件级立即拉回登录页——替代纯轮询（0.3s 间隙内白屏/
    乱跳窗口期，用户实测「点两下变空白」根因）。返回移除函数。

    同步函数（勿 await）：framenavigated 为同步回调，内部只做 URL 判定 +
    schedule 拉回任务；仅拦主 frame（登录页内的验证码 iframe 等不受影响）；
    仅拦小红书域（守卫自己 load_url 登录页属 /login 不触发）。登录成功/监视
    退出时由调用方移除监听，避免循环。
    """
    _XHS = "xiaohongshu.com"

    def _on_nav(frame) -> None:
        try:
            if not frame.is_main_frame():
                return
            url = frame.url or ""
        except Exception:  # noqa: BLE001（frame 已销毁：忽略）
            return
        if _XHS in url and "/login" not in url:
            if stop.is_set():
                return
            print(f"[xhs-leads][guard] 导航拦截：离开登录页（{url[:70]}），立即拉回", flush=True)
            try:
                asyncio.get_running_loop().create_task(win.load_url(XHS_LOGIN_URL))
            except Exception:  # noqa: BLE001（loop 关闭中：轮询兜底）
                pass

    try:
        win.page.on("framenavigated", _on_nav)
        return lambda: win.page.remove_listener("framenavigated", _on_nav)
    except Exception:  # noqa: BLE001（页面未就绪：轮询兜底）
        return None


async def _guard_monitor_forced(win: "ShellWindow", base: str, cs, state) -> bool | None:
    """强制登录单步：返回 True=登录成功 / None=继续监视（离开登录页自动拉回）。

    R-6.48k：移除每 10s 轮询补注——登录页行为（弹窗锁/居中/标题）由
    add_init_script 在每个新 document 自动注入（事件驱动），无需循环守护。
    """
    has_id = has_id_token(cs)
    if has_id:
        return await _guard_handle_id_token(win, base, cs, state)
    try:
        cur = await win.current_url()
    except Exception:  # noqa: BLE001
        cur = ""
    if cur and "/login" not in cur:
        print(f"[xhs-leads][guard] 离开登录页（{cur[:70]}），强制拉回登录页", flush=True)
        try:
            await win.load_url(XHS_LOGIN_URL)
        except Exception:  # noqa: BLE001
            pass
        # 拉回产生新 document：事件点补注（init script 已注册时仅 evaluate 当前文档）
        await _ensure_login_injects(win)
    return None


async def _guard_monitor_loop(win: "ShellWindow", base: str, stop: "threading.Event", cur_force: bool) -> bool:
    """小红书页内监视循环：强制=锁死拉回；主动=可关闭。返回 ok（登录成功回 APP）。"""
    state = {"import": 0.0}
    # R-6.48h：强制登录挂事件级导航拦截（离开 /login 立即拉回）；退出时移除。
    remove_nav_hook = _guard_hook_forced_nav(win, stop) if cur_force else None
    try:
        while not stop.is_set():
            if await _rebind_if_closed(win, base):
                continue
            try:
                cs = await win.cookies()
            except Exception:  # noqa: BLE001
                cs = []
            step = _guard_monitor_voluntary if not cur_force else _guard_monitor_forced
            result = await step(win, base, cs, state)
            if result is not None:
                return result
            # R-6.48q：登录成功检测降频 0.3→1s（扫码登录本身秒级，0.3s 精度无意义，
            # 减少登录页期间高频 cookies 读取）
            await asyncio.sleep(1.0)
        return False
    finally:
        if remove_nav_hook is not None:
            try:
                remove_nav_hook()
            except Exception:  # noqa: BLE001（loop 关闭中：忽略）
                pass


async def _guard_return_to_app(win: "ShellWindow", base: str) -> None:
    """真登录成功回前端（强制登录：失败/中途退出不回）。"""
    for _attempt in range(3):
        try:
            await win.load_url(f"{base}/")
        except Exception:  # noqa: BLE001
            pass
        try:
            await win.evaluate_js("window.location.href = '%s'" % f"{base}/")
        except Exception:  # noqa: BLE001
            pass
        try:
            cur = await win.current_url()
            if cur.startswith(base):
                return  # 已切回 APP
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.0)


async def _guard_startup_status(base: str) -> None:
    """启动时按 login_status 判定三态。R-6.48p（用户最新拍板，覆盖 R-6.48i）：
    启动未登录/被踢**不再自动强制导航登录页**——壳窗保持 APP 界面，前端
    ForceLogin 登录提示框自动弹出（App.vue onMounted 检测），用户点「去登录」
    才经 login_begin(pending)→守卫导航小红书登录页。已登录启动不受影响
    （守卫待命，仅响应登录引导 pending）。"""
    ls = await _guard_get_json(base, "/api/login_status")
    kicked = bool(ls and ls.get("kicked"))
    if ls and ls.get("logged_in") and not kicked:
        print("[xhs-leads][guard] 启动已登录——守卫待命（仅响应登录引导 pending）", flush=True)
        return
    reason = "启动检测到登录已被平台踢出" if kicked else "启动未登录"
    print(
        f"[xhs-leads][guard] {reason}——壳窗保持 APP 界面，"
        "等待前端登录提示框「去登录」触发导航（不再自动跳转登录页）", flush=True,
    )


async def _guard_wait_pending(win: "ShellWindow", base: str, stop: "threading.Event") -> bool | None:
    """轮询 login_begin_state：pending 出现返回 force(bool)，否则 None（继续外层）。"""
    st = await _guard_get_json(base, "/api/auth/login_begin_state")
    if not (st and st.get("pending")):
        return None
    return bool(st.get("force"))


async def _guard_announce_and_navigate(win, base, force) -> None:
    """R-6.37i 拆自 login_guard：打印切换日志并导航到登录页（R-6.21e 只响应 pending）。"""
    print(
        "[xhs-leads][guard] 切换到小红书登录页"
        f"（{'强制登录·不可关闭' if force else '主动重登·可关闭'}）", flush=True,
    )
    await _guard_navigate_to_login(win, base, force)


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
        # R-6.48q：pending 事件驱动——login_begin 置位 evt 才醒来（同进程同 loop）。
        # 守卫无条件 clear 消费；pending 被 cancel 清掉（login_cancel）时不误导航。
        if gate is not None and gate.get("evt") is not None:
            await gate["evt"].wait()
            gate["evt"].clear()
            if not gate.get("pending"):
                continue  # 事件已消费但 pending 被清：不导航，回挂起
            force = bool(gate.get("force"))
        else:
            # 退化路径（gate 未注入：测试/无壳直跑）保留低频轮询
            await asyncio.sleep(1.5)
            force = await _guard_wait_pending(win, base, stop)
            if force is None:
                continue
        # R-6.19：Tab1 被关闭后 keeper 即时重建——守卫重绑定新壳 page，重建即重置登录状态
        if await _rebind_if_closed(win, base):
            in_login = False
            continue
        if in_login:
            continue  # 正在登录页（局部轮询控制）
        await _guard_announce_and_navigate(win, base, force)
        in_login = True
        ok = await _guard_monitor_loop(win, base, stop, force)
        in_login = False
        if ok:
            await _guard_return_to_app(win, base)

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
                print("[xhs-leads][guard] login_guard 已重绑定重建的壳 tab", flush=True)
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
        if not p.is_closed() and u.startswith(base) and "login-hint" not in u:
            coord.register_shell(p)
            return p
    return None


def _match_tab2_page(p, shell, coord):
    """R-6.37g 拆自 _keeper_find_tab2：单页是否 Tab2（explore 或 login-hint）。"""
    if p is shell or p.is_closed():
        return None
    u = p.url or ""
    if ("xiaohongshu.com" in u and "/login" not in u) or "login-hint" in u:
        print(f"[xhs-leads][tab2] _find_tab2 命中: url={u[:60]!r}", flush=True)
        coord.register_tab2(p)
        return p
    return None


async def _keeper_find_tab2(ctx, shell, coord):
    """采集窗（Tab2）可能在 explore 或 /login-hint，两者都算任务 tab。"""
    alive = coord.tab2()
    if alive is not None and alive is not shell:
        return alive
    for p in ctx.pages:
        t2 = _match_tab2_page(p, shell, coord)
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
    print("[xhs-leads][keeper] 壳窗口初始创建完成（R-6.22p 关主窗=退出应用）", flush=True)


async def _keeper_tab2_target_url(config, engine_pool):
    """Tab2 目标 URL（R-6.49 用户判据：登录→login、采集→explore；启动路径禁 explore）：
    无论登录态，Tab2 兜底 /login-hint（已登录时页面内动态显示「已登录·开始采集」）。
    永不返回 APP 首页/explore——APP 首页是壳窗内容（登录后右窗出现 APP 主界面=R-6.50
    回归），explore 仅由采集 open_tab2_window 或 login-hint 页内「开始采集」触发。"""
    base = f"http://127.0.0.1:{config.front_port()}"
    return base + "/login-hint"


async def _keeper_sweep_blank(ctx, shell) -> int:
    """清理空白/残留 tab（壳窗引用跳过）。

    R-6.48o：扩展 chrome://newtab 清理——引擎崩溃后 Chrome 会话恢复会把崩溃前
    的标签全部还原，新上下文首个默认页 chrome://newtab 也在此列；此前只清
    ""/about:blank 导致崩溃恢复后残留空标签（用户实测：崩溃重启后多出空白窗口）。
    R-6.53t：返回关闭数并打日志（用户手动新标签 5s 内被关=「自动化操作」取证）；
    调用侧已降频到 30s，避免高频清理与手动浏览打架。
    """
    closed = 0
    for p in list(ctx.pages):
        if p is shell or p.is_closed():
            continue
        u = (p.url or "").strip()
        if u in ("", "about:blank", "chrome://newtab", "chrome://newtab/"):
            await _safe_close(p)
            closed += 1
            print(f"[xhs-leads][keeper] 清理空白/新标签页 url={u!r}", flush=True)
        elif "xiaohongshu.com" in u and "/login" in u:
            await _safe_close(p)
            closed += 1
            print(f"[xhs-leads][keeper] 清理残留登录页 url={u!r}", flush=True)
    return closed


async def _keeper_recovery_cleanup(ctx, shell) -> int:
    """崩溃恢复清理：壳窗在监护期间失效后重新找到 → 关闭所有非主窗口残留。

    R-6.48o：引擎崩溃 → Chrome 会话恢复还原全部标签（调试窗 Tab2、残留页）。
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
        print(
            "[xhs-leads][keeper] keeper 页面集="
            + " | ".join((p.url or "")[:45] for p in ctx.pages if not p.is_closed())
            + "  shell_ref=" + ((_sh.url or "")[:30] if _sh else "None"), flush=True,
        )
    return last[0]


async def _calibrate_reused_tab2(t2, _t2_url, coord, engine_pool) -> None:
    """R-6.37i 拆自 _keeper_setup_tab2：复用残留 Tab2 时校准窗口并注册监听。

    R-6.49：采集页（xiaohongshu.com）保持不动（合法采集目标，禁止启动路径导航走）；
    其余页按目标（未登录 login-hint / 已登录 APP 首页）校准。"""
    try:
        if _t2_url and "login-hint" not in (t2.url or "") and "xiaohongshu.com" not in (t2.url or ""):
            await t2.goto(_t2_url, wait_until="domcontentloaded", timeout=10000)
        # R-6.48h：复用残留路径同样补标题守护（此前仅新建路径注入——重启保留的
        # Tab2 裸奔显示 login-hint 原始标题「尚未登录小红书」）
        await _ensure_debug_title(t2)
        win_w2, win_h2, _, win_left2 = engine_pool.shell_window_geometry()
        await _calibrate_window(t2, win_left2, 0, win_w2, win_h2)
        coord.register_tab2(t2)
        coord.watch(t2, is_shell=False)
    except Exception as e:  # noqa: BLE001
        print(f"[xhs-leads][tab2] Tab2 采集窗口复用校准失败: {e!r}", flush=True)


async def _keeper_setup_tab2(ctx, eng, coord, engine_pool, config):
    """初始块 Tab2：复用残留 explore/login-hint 或新建采集窗（失败延迟重试 1 次）。"""
    async with engine_pool.TAB2_GUARD_LOCK:
        _t2_url = await _keeper_tab2_target_url(config, engine_pool)
        t2 = await _keeper_find_tab2(ctx, coord.shell(), coord)
        if t2 is not None:
            await _calibrate_reused_tab2(t2, _t2_url, coord, engine_pool)
            return
        for _attempt in range(2):
            try:
                # R-6.49c：启动路径 Tab2 走 open_tab2_window 应用模式（--app 无地址栏/标签条），
                # 与采集路径形态一致——不再 new_page 建普通 tab（带地址栏，用户反馈
                # 「采集调试窗口变了、出现地址栏、不是 app 形态」）。
                from src.engine_types import builtin_cft_candidates  # noqa: PLC0415
                _cfts = builtin_cft_candidates()
                # R-6.53p：builtin_cft_candidates() 返回 str（单个路径）而非列表——
                # 旧写法 _cfts[0] 取到首字符 '/'，Popen 用 '/' 当引擎路径 →
                # PermissionError(13) → 初始 Tab2（login-hint 窗）创建 3 次全失败。
                _exec = _cfts if _cfts else None
                page = await open_tab2_window(
                    ctx,
                    coord.shell(),
                    url=_t2_url or f"http://127.0.0.1:{config.front_port()}/login-hint",
                    user_data_dir=eng.user_data_dir,
                    executable=_exec,
                )
                # open_tab2_window 已内建 goto/login-hint、标题守护、窗口校准
                coord.register_tab2(page)
                coord.watch(page, is_shell=False)
                return
            except Exception as e:  # noqa: BLE001
                if _attempt == 0:
                    print(f"[xhs-leads][tab2] Tab2 采集窗口首次创建失败（延迟重试）: {e!r}", flush=True)
                    await asyncio.sleep(4.0)
                else:
                    print(f"[xhs-leads][tab2] Tab2 采集窗口初始创建失败（可稍后经设置开关重开）: {e!r}", flush=True)


async def _keeper_init_pass(base, coord, config, engine_pool, last):
    """初始块：确保 Tab1 存在挂监听；Tab2 按开关初始创建一次；清理空白残留。"""
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
        await _keeper_setup_tab2(ctx, eng, coord, engine_pool, config)
    await _keeper_sweep_blank(ctx, coord.shell())


# R-6.53t：空白清理 30s 节流状态（跨 keeper 轮询保持）
_KEEPER_SWEEP_LAST = [0.0]


async def _keeper_link_tab2(t2, coord, ctx, config, engine_pool):
    """Tab2 登录态联动（R-6.49 用户判据：登录→login、采集→explore；启动路径禁 explore）：
    未登录 → 拉回 login-hint（含采集残留页）；已登录 → 采集页（xiaohongshu.com）保持、
    其余本地页校准到 APP 首页。explore 不再由 keeper 主动打开——仅采集 open_tab2_window 触发。

    R-6.53n 修复：_keeper_tab2_target_url 自 R-6.49 起恒返回 /login-hint（"无论登录态兜底"），
    原保持条件 `"xiaohongshu.com" in u and "login-hint" not in _url` 中后项恒 False → 死代码，
    采集中的 search_result/explore 页每轮被 goto 拉回 login-hint → 采集器解析提示页无卡片 → 0 条
    （notes 自 R-6.49 起从未落库，与 21:32/21:38 两次采集 0 条取证一致）。
    改为：小红书域页（采集搜索页/explore/登录墙页）一律保持，不拉回。"""
    coord.register_tab2(t2)
    coord.watch(t2, is_shell=False)
    try:
        _url = await _keeper_tab2_target_url(config, engine_pool)
        u = t2.url or ""
        if "xiaohongshu.com" in u:
            return  # 小红书域页（采集搜索页/explore/登录墙页）：保持，不拉回 login-hint
        if u != _url and u.rstrip("/") != _url.rstrip("/"):
            await t2.goto(_url, wait_until="domcontentloaded", timeout=8000)
        # R-6.53t：不再每轮 close_orphan_explore——R-6.28 起 login_status 已不 new_page
        # 开 explore 判定页，此调用只剩每 5s 误关用户手动打开的第二个 explore 标签的副作用
        # （用户实测「右侧窗口似乎有一些自动化操作」根因之一）。
    except Exception:  # noqa: BLE001（导航失败不阻断监护，下轮再试）
        pass


def _server_has_running_collect() -> bool:
    """R-6.53o：采集执行期间 keeper 暂停 tab2 治理/崩溃清理，避免与采集器 CDP
    并发触发引擎假死、且崩溃恢复清理误关采集中的搜索页。延迟导入规避循环依赖。"""
    try:
        from src import server as _server  # noqa: PLC0415（运行时 server 已加载，取任务注册表）
        return any(st.get("status") == "running" for st in _server._COLLECT_TASKS.values())
    except Exception:  # noqa: BLE001（状态源不可用视为不采集，keeper 正常治理）
        return False


async def _keeper_poll_pass(base, coord, stop, on_shell_closed, config, engine_pool, last) -> str:
    """兜底轮询单轮：壳窗缺失（close 事件丢失）→ 触发应用退出；Tab2 登录态联动。返回 'exit'|'continue'。"""
    try:
        eng = await engine_pool.pool.get("default")
        ctx = await eng.context()
        collecting = _server_has_running_collect()
        if collecting:
            print("[xhs-leads][keeper] 采集执行中：暂停 tab2 治理/清理（避免 CDP 并发与误关采集页）", flush=True)
        else:
            _keeper_log_snapshot(ctx, coord, last)
        # R-6.48o：崩溃恢复检测——壳窗引用失效（引擎崩溃/会话重建）后扫描重新
        # 找到 → 判定为崩溃恢复，关闭所有非主窗口残留（is_closed 判活盲区见
        # _keeper_shell_effective：引用不在当前页面集即视为失效）。
        was_dead = _keeper_shell_effective(coord, ctx) is None
        shell = await _keeper_find_shell(ctx, base, coord)
        if was_dead and shell is not None:
            if collecting:
                print("[xhs-leads][keeper] 采集执行中：跳过崩溃恢复清理（保留采集页）", flush=True)
            else:
                n = await _keeper_recovery_cleanup(ctx, shell)
                if n:
                    print(f"[xhs-leads][keeper] 崩溃恢复：关闭 {n} 个残留窗口，仅保留主窗口", flush=True)
        if shell is not None:
            coord.register_shell(shell)
            coord.watch(shell, is_shell=True)
        elif coord.shell() is None:
            # R-6.53b：引擎崩溃自动重建后壳窗缺失（新引擎进程内没有旧壳窗）——
            # 重建时间窗内重开壳窗，避免 keeper 误判「主窗已关闭→退出应用」；
            # 非重建场景（用户真关主窗且 close 事件丢失）维持原退出语义。
            eng = await engine_pool.pool.get("default")
            if eng.rebuild_age() < 60:
                # R-6.53d：引擎重建需要数秒，壳窗重开可能在引擎就绪前调用——
                # 失败不退出，下轮重试；60s 重建窗口过后自然走下方退出分支收敛。
                try:
                    shell = await _keeper_rebuild_shell(ctx, base, coord, engine_pool)
                except Exception as e:  # noqa: BLE001（重建未就绪：下轮重试）
                    print(f"[xhs-leads][keeper] 引擎重建期壳窗重开暂未就绪（下轮重试）: {e!r}", flush=True)
                    shell = None
                if shell is not None:
                    print("[xhs-leads][keeper] 引擎重建，壳窗已重开，应用继续", flush=True)
                else:
                    print("[xhs-leads][keeper] 引擎重建期壳窗未就绪，下轮轮询重试（不退出）", flush=True)
                    return "continue"
            elif coord.shell_close_age() < 1e17 and eng.is_connected_now():
                # R-6.53f：壳窗 close 事件已有记录且引擎存活（非崩溃）→ 用户真关主窗
                print("[xhs-leads][keeper] 兜底轮询确认主窗已关闭（引擎存活），触发应用退出", flush=True)
                on_shell_closed()
                return "exit"
            elif on_shell_closed is not None:
                print("[xhs-leads][keeper] 兜底轮询确认主窗已关闭，触发应用退出", flush=True)
                on_shell_closed()
                return "exit"
        if not collecting:
            t2 = await _keeper_find_tab2(ctx, coord.shell(), coord)
            if t2 is not None:
                await _keeper_link_tab2(t2, coord, ctx, config, engine_pool)
            # R-6.53t：空白清理降频 5s→30s（高频清理与用户手动浏览打架，
            # 用户实测「右侧窗口似乎有一些自动化操作」根因之一）。
            if time.monotonic() - _KEEPER_SWEEP_LAST[0] >= 30.0:
                _KEEPER_SWEEP_LAST[0] = time.monotonic()
                try:
                    n = await _keeper_sweep_blank(ctx, coord.shell())
                    if n:
                        print(f"[xhs-leads][keeper] 空白清理关闭 {n} 个页面", flush=True)
                except Exception:  # noqa: BLE001（清理失败不阻断监护，下轮再试）
                    pass
    except Exception:  # noqa: BLE001（引擎未就绪：下轮再试）
        pass
    return "continue"


async def keep_shell_tab(base_url: str, stop: "threading.Event", on_shell_closed: "Callable[[], None] | None" = None) -> None:
    """R-6.22p 窗口监护：关主窗=关闭应用（on_shell_closed 退出）；关采集窗=仅关采集窗不复活。

    初始块确保 Tab1/Tab2 创建一次；兜底轮询 5s 检测壳窗缺失。R-6.36e：
    嵌套闭包提升为上方 _keeper_* 模块级助手，控制流与语义逐字保留。
    """
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    base = base_url.rstrip("/")
    coord = get_coordinator()
    coord.bind(stop, on_shell_closed)
    coord.attach_engine(engine_pool.pool)  # R-6.53h：传 EnginePool 单例实例（此前误传模块导致 _shell_closed_verify AttributeError）
    last = [frozenset()]  # R-6.28：页面集变化才打印快照

    # 初始：确保 Tab1 存在并挂监听；Tab2 按开关初始创建一次
    while not stop.is_set():
        try:
            await _keeper_init_pass(base, coord, config, engine_pool, last)
            break
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                print(f"[xhs-leads][keeper] Tab1 初始化监护暂不可用（重试）: {e!r}", flush=True)
            await asyncio.sleep(2.0)

    # 兜底轮询：壳窗缺失（close 事件丢失场景）→ 触发应用退出；仅监护不重建
    while not stop.is_set():
        await asyncio.sleep(5.0)
        if await _keeper_poll_pass(base, coord, stop, on_shell_closed, config, engine_pool, last) == "exit":
            break