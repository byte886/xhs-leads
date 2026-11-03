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
        await page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    return page


async def _ensure_debug_title(page) -> None:
    """采集调试窗口标题守护：Chrome 窗口标题跟随 document.title。采集页为真实
    小红书页（SPA 路由/整页导航都会重设 title），用轻量 setInterval 覆盖为
    「XHS Leads · 采集调试窗口」，让用户一眼看出这是调试/观察窗口。
    title 为纯浏览器元数据：不触发网络请求、不改页面逻辑与 DOM 结构，
    风控面极小（远小于既有 stealth 注入）。

    采用 **page 级 add_init_script**（Playwright 仅对该 page 的后续导航生效，
    不污染壳窗口/其他页面）+ 当前文档立即生效；防重标记避免重复 add 累积。
    幂等，可反复调用（已装时仅刷新当前文档）。
    """
    if getattr(page, "_debug_title_installed", False):
        try:
            await page.evaluate(_COLLECT_DEBUG_TITLE_JS)
        except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
            pass
        return
    try:
        await page.add_init_script(_COLLECT_DEBUG_TITLE_JS)
        await page.evaluate(_COLLECT_DEBUG_TITLE_JS)
        page._debug_title_installed = True
    except Exception:  # noqa: BLE001（页面未就绪/已关闭：下次调用重试）
        pass


# 采集调试窗口标题守护脚本（页面级 init script，注入即常驻）
_COLLECT_DEBUG_TITLE_JS = """() => {
    const T = 'XHS Leads · 采集调试窗口';
    const setT = () => { if (document.title !== T) document.title = T; };
    setT();
    setInterval(setT, 2000);
}"""

# 真实登录页（R-6.5→R-6.6 强制登录）：直接导航小红书**独立整页登录页**。
# 登录成功后小红书自动跳转 redirectPath（首页），守卫捕获 web_session 注入引擎后回 APP。
XHS_LOGIN_URL = (
    "https://www.xiaohongshu.com/login"
    "?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F"
)

# R-6.9 登录成功过渡提示：id_token 一出现即注入「等待中」提示，遮挡小红书登录后页面、
# 拦截用户误操作；引擎注入完成 load_url 切回 APP 时随导航消失。幂等（已有 id 不重复注入）。
_WAITING_OVERLAY_JS = r"""(() => {
  if (document.getElementById('xhs-leads-waiting')) return;
  const d = document.createElement('div');
  d.id = 'xhs-leads-waiting';
  d.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;'
    + 'background:rgba(255,255,255,.55);backdrop-filter:blur(2px);'
    + 'z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;';
  const st = document.createElement('style');
  st.textContent = '@keyframes xhs-leads-spin{to{transform:rotate(360deg)}}';
  const t = document.createElement('div');
  t.textContent = '登录成功，正在进入工作台…';
  t.style.cssText = 'font-size:18px;color:#333;background:rgba(255,255,255,.92);'
    + 'padding:10px 20px;border-radius:10px;margin-bottom:16px;letter-spacing:1px;'
    + 'box-shadow:0 2px 12px rgba(0,0,0,.12);';
  const s = document.createElement('div');
  s.style.cssText = 'width:26px;height:26px;border:3px solid #ff2442;border-top-color:transparent;'
    + 'border-radius:50%;animation:xhs-leads-spin 1s linear infinite;background:rgba(255,255,255,.92);'
    + 'padding:4px;background-clip:content-box;box-shadow:0 2px 12px rgba(0,0,0,.12);';
  d.appendChild(st); d.appendChild(t); d.appendChild(s);
  document.body.appendChild(d);
})()"""
_REMOVE_WAITING_OVERLAY_JS = (
    "(() => { const e = document.getElementById('xhs-leads-waiting'); if (e) e.remove(); })()"
)

# 登录 UI 改版（用户拍板）：主动重登（设置页「重新登录」force=False）登录页**可关闭**——
# 注入右上角「✕」返回 APP（登录态不变）；强制登录（force=True）不注入，保持无叉。
_ACTIVE_CLOSE_JS = (
    "(function(){if(window.__xhs_leads_active_close)return;window.__xhs_leads_active_close=true;"
    "var d=document.createElement('div');d.id='xhs-leads-active-close';d.textContent='✕';"
    "d.style.cssText='position:fixed;top:16px;right:16px;width:34px;height:34px;"
    "border-radius:50%;background:rgba(255,255,255,.94);box-shadow:0 1px 8px rgba(0,0,0,.2);"
    "display:flex;align-items:center;justify-content:center;font-size:15px;color:#555;"
    "cursor:pointer;z-index:2147483646;font-family:-apple-system,\"PingFang SC\",sans-serif;';"
    "d.onclick=function(){window.location.href='__BASE__';};"
    "document.body.appendChild(d);})()"
)

# R-6.10 首页模式：点开小红书首页右上角「登录」面板（二维码浮于首页背景上）。
# 历史：本常量自 R-6.10 引入调用后从未定义（NameError 被 try 吞掉，静默降级整页登录页），
# 本次重建——尽力注入，找不到按钮/注入失败时仍由 load_url 整页登录兜底（不新增风险）。
_OPEN_LOGIN_PANEL_JS = (
    "(function(){"
    "var btns=[].slice.call(document.querySelectorAll('a,div,span,button'));"
    "var el=btns.find(function(e){var t=(e.textContent||'').trim();"
    "return t==='登录'&&e.offsetWidth>0&&e.offsetHeight>0;});"
    "if(el){el.click();return 'clicked';}return 'no-login-btn';})()"
)


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
                    f"[xhs-leads] --app 窗口路由失败（第{attempt + 1}/3 次）: {e!r}\n"
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
        f"[xhs-leads] 窗口校准失败（{left},{top} {width}x{height}，"
        f"重试 3 次仍失败）: {last_err!r}",
        flush=True,
    )


async def ensure_shell(base_url: str):
    """强制引擎有头启动，打开前端 tab 并置前（壳窗口 = CFT 引擎窗口）。"""
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    if config.get_config().get("headless"):
        config.set_config("headless", False)  # 壳必须有头（A 路径常态）
    eng = await engine_pool.pool.get("default")
    ctx = await eng.context()
    base = base_url.rstrip("/")
    page = None
    for p in ctx.pages:
        if (p.url or "").startswith(base) and not p.is_closed():
            page = p
            break
    if page is None:
        # R-6.19：复用引擎默认空白页（launch_persistent_context 不带 url → 首个 tab 为
        # about:blank），避免「默认空白 tab 残留」造成窗口第 3 个多余 tab；无空白页才新开。
        page = next(
            (
                p for p in ctx.pages
                if not p.is_closed() and (p.url or "").strip() in ("", "about:blank")
            ),
            None,
        )
        if page is None:
            page = await ctx.new_page()
    # 重启竞态：uvicorn 刚起时静态路由可能瞬时未就绪——goto 重试（≤20s），
    # 成功后壳 tab 停在 APP 前端页；守卫在未登录时自行接管导航。
    last_err = None
    for _ in range(40):
        try:
            await page.goto(base, wait_until="domcontentloaded")
            if (page.url or "").startswith(base):
                break
        except Exception as e:  # noqa: BLE001（后端就绪前 goto 会失败，下轮重试）
            last_err = e
        await asyncio.sleep(0.5)
    else:
        print(f"[xhs-leads] 壳 tab 导航前端未成功（最后错误: {last_err!r}）", flush=True)
    # R-6.19：导航成功后清理其余空白残留 tab（启动默认页已复用，此处仅清理异常残留）
    for p in list(ctx.pages):
        if p is not page and not p.is_closed() and (p.url or "").strip() in ("", "about:blank"):
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                pass
    # R-6.22 双窗口左右分屏：壳窗口（主 APP）放主屏左半
    win_w, win_h, _, _ = engine_pool.shell_window_geometry()
    # R-6.22c：壳窗口应用模式——launch_persistent_context 的 --app 参数实测不生效
    # （窗口仍是带菜单栏/标签栏的普通 Chrome 窗口）。统一改走 --app 命令行路由
    # （与采集调试窗口同法）：同实例弹出应用模式窗口（无菜单栏/标签栏/地址栏），
    # 成功后关闭初始普通窗口（keep_shell_tab 此时尚未启动，close 无监听者，无竞态）。
    from src import browser_client  # noqa: PLC0415

    cfts = browser_client._builtin_cft_candidates()
    app_page = await _launch_app_window(
        ctx, base,
        user_data_dir=eng.user_data_dir,
        executable=cfts if cfts else None,  # 返回值为单个路径 str（非列表），勿取 [0]
        left=0, width=win_w, height=win_h,
    )
    if app_page is not None:
        try:
            await app_page.goto(base, wait_until="domcontentloaded", timeout=12000)
        except Exception:  # noqa: BLE001（--app=base 已带 URL，goto 仅保险）
            pass
        await _calibrate_window(app_page, 0, 0, win_w, win_h)
        try:
            await app_page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        # 关闭初始普通窗口 tab（keep_shell_tab 未启动无监听；后续监护 _find_shell
        # 按 URL 找到应用模式壳窗口）
        for p in list(ctx.pages):
            if (
                p is not app_page and not p.is_closed()
                and (p.url or "").startswith(base)
            ):
                try:
                    await p.close()
                except Exception:  # noqa: BLE001
                    pass
        page = app_page
    else:
        # 路由不可用：保留初始普通窗口（能力兜底），仅校准
        await _calibrate_window(page, 0, 0, win_w, win_h)
    try:
        await page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    return ShellWindow(page, base)


async def login_guard(win: ShellWindow, base_url: str, stop: "threading.Event") -> None:
    """登录守卫（R-6.5 真实登录页方案；R-6.6 强制登录；R-6.9 主动重登；R-6.10 首页模式）。

    语义（用户拍板）：
    - **启动不自动导航**：未登录 / 启动被踢不停在登录页，由 APP 前端强制登录对话框承接；
      用户点「去登录」（login_begin force=True）→ 本守卫才导航小红书真实登录页（无叉）。
    - **运行时被踢不直接导航**：dispatchEvent xhs-leads:kicked 通知前端弹强制登录对话框。
    - **主动重登可关闭**：force=False → 登录页注入「✕」，点关闭回 APP，登录态不变。
    - **强制登录不可关闭**：force=True → force_login_js 锁死 + URL 拉回。
    - 登录成功链路不变：id_token 出现 → 等待遮罩 → import_cookies 注入引擎 → 回 APP。
    CZ_SKIP_LOGIN_GUARD=1 可跳过（自动化路径）。
    """
    if os.environ.get("CZ_SKIP_LOGIN_GUARD"):
        return
    import threading  # noqa: PLC0415

    base = base_url.rstrip("/")

    # 强制登录 JS（R-6.6→R-6.10）：防御性锁定登录页**明确的关闭按钮**，但**绝不遮蔽页面内容**。
    force_login_js = (
        "(function(){"
        "if(window.__xhs_leads_lock_ok)return;window.__xhs_leads_lock_ok=true;"
        "var st=document.createElement('style');st.id='xhs-leads-lock';"
        "st.textContent='[aria-label=\"关闭\"]{display:none!important}[aria-label=\"close\"]{display:none!important}.close-btn{display:none!important}';"
        "document.head.appendChild(st);"
        "new MutationObserver(function(){"
        "var a=document.querySelectorAll('[aria-label=\"关闭\"],[aria-label=\"close\"],.close-btn');"
        "for(var i=0;i<a.length;i++){a[i].style.display='none';a[i].removeAttribute('onclick');}"
        "}).observe(document.body,{childList:true,subtree:true});})();"
    )

    # 登录页窗口适配 JS（R-6.26c，用户反馈「登录页随窗口大小自动居中，需要滚动条就出现」）：
    # 小红书登录页是 fixed 遮罩（.reds-modal）+ reds-lock-scroll 锁滚动——窗口小于内容高度时
    # 卡片底部被裁且无滚动条。适配：解除锁滚动 + 遮罩限高视口并允许纵向滚动 + 内容超高时
    # 顶部对齐（初始可见头部）、未超高时垂直居中。幂等 + resize/MutationObserver/setInterval 兜底。
    login_fit_js = (
        "(function(){"
        "if(window.__xhs_leads_login_fit)return;window.__xhs_leads_login_fit=true;"
        "var st=document.createElement('style');st.id='xhs-leads-login-fit';"
        "st.textContent='html,body{overflow:auto!important}.reds-modal{overflow-y:auto!important;max-height:100vh!important;height:auto!important;min-height:100vh!important}.login-container{flex:none!important}';"
        "document.head.appendChild(st);"
        "function fit(){"
        "var m=document.querySelector('.reds-modal');var c=document.querySelector('.login-container');"
        "if(!m||!c)return;"
        "var need=c.getBoundingClientRect().height+24>window.innerHeight;"
        "m.style.alignItems=need?'flex-start':'center';"
        "}"
        "fit();"
        "window.addEventListener('resize',fit);"
        "new MutationObserver(fit).observe(document.body,{childList:true,subtree:true});"
        "setInterval(fit,2000);"
        "})();"
    )

    def _inject_force_login() -> None:
        try:
            # R-6.22g：evaluate_js 是协程——未 await 时协程从不执行（RuntimeWarning），
            # 强制拉回实际无效；这里创建任务由事件循环执行。
            _loop = asyncio.get_running_loop()
            _loop.create_task(_safe_force_login())
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 强制登录 JS 注入暂未成功: {e!r}", flush=True)

    async def _safe_force_login() -> None:
        try:
            await win.evaluate_js(force_login_js)
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 强制登录 JS 注入暂未成功: {e!r}", flush=True)

    # R-6.26c：登录页窗口适配注入（居中 + 需要滚动条就出现）——幂等，未就绪静默等补注
    def _inject_login_fit() -> None:
        try:
            _loop = asyncio.get_running_loop()
            _loop.create_task(_safe_login_fit())
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 登录页适配 JS 注入暂未成功: {e!r}", flush=True)

    async def _safe_login_fit() -> None:
        try:
            await win.evaluate_js(login_fit_js)
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 登录页适配 JS 注入暂未成功: {e!r}", flush=True)

    async def _inject_active_close() -> None:
        try:
            await win.evaluate_js(_ACTIVE_CLOSE_JS.replace("__BASE__", f"{base}/"))
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 主动重登关闭按钮注入暂未成功: {e!r}", flush=True)

    async def _get_json(path: str):
        # R-6.16 关键修复：守卫与 uvicorn 同事件循环后，同步 urllib 每 0.5s 轮询会
        # 阻塞整个 loop（login_status 等协程排队超时）。改 asyncio.to_thread 脱离 loop。
        try:
            return await asyncio.to_thread(
                lambda: _json_body(base + path, timeout=3)
            )
        except Exception:  # noqa: BLE001
            return None

    async def _post_json(path: str, payload: dict):
        try:
            return await asyncio.to_thread(
                lambda: _json_post(base + path, payload, timeout=15)
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # 壳 tab 已就绪（ensure_shell 已 goto 前端）；登录状态判定
    ls = await _get_json("/api/login_status")
    kicked_at_start = bool(ls and ls.get("kicked"))
    if ls and ls.get("logged_in") and not kicked_at_start:
        print("[xhs-leads] 启动已登录——守卫待命（仅响应登录引导 pending）")
    elif kicked_at_start:
        print("[xhs-leads] 启动检测到登录已被平台踢出——APP 前端显示强制登录对话框")
    else:
        print("[xhs-leads] 启动未登录——APP 前端显示强制登录对话框")

    in_login = False
    cur_force = False
    while True:
        if stop.is_set():
            return
        await asyncio.sleep(0.5)
        # R-6.19：Tab1 被关闭后 keeper 即时重建——登录守卫重绑定新壳 page，
        # 重建即重置登录状态（旧导航流程已随旧 tab 失效）
        if await _rebind_if_closed(win, base):
            in_login = False
            continue
        if in_login:
            continue  # 正在登录页（局部轮询控制）
        st = await _get_json("/api/auth/login_begin_state")
        pending = bool(st and st.get("pending"))
        pending_force = bool(st and st.get("force"))
        # R-6.21e（用户拍板）：移除运行时 login_status 被踢轮询——登录失效由**交互异常
        # 驱动**（采集/发布/客户失败 detail 携带登录信号 → 前端弹强制登录框 → login_begin
        # pending → 本守卫导航登录页）。守卫只响应 pending（登录引导），不再后台刷登录态。
        if pending:
            cur_force = pending_force
            print(
                "[xhs-leads] 切换到小红书登录页"
                f"（{'强制登录·不可关闭' if cur_force else '主动重登·可关闭'}）"
            )
            # R-6.22g：强制登录导航前清除壳窗口 xiaohongshu Cookie——登录页若检测到
            # 已登录会自动跳过界面（风控会话 Cookie 有效），用户将无法扫码验证；
            # 清除后登录页呈现扫码/手机号界面，完成主动重新登录解除风控/被踢。
            if cur_force:
                try:
                    n = await win.clear_domain_cookies("xiaohongshu.com")
                    print(f"[xhs-leads] 强制登录前清除壳窗口小红书 Cookie {n} 条")
                except Exception as e:  # noqa: BLE001
                    print(f"[xhs-leads] 清除壳窗口 Cookie 异常: {e!r}")
        else:
            continue

        # ---- 导航到小红书真实登录页（双保险） ----
        try:
            await win.load_url(XHS_LOGIN_URL)
            print("[xhs-leads] load_url 已调用（无异常）")
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] load_url 异常: {e!r}")
        try:
            await win.evaluate_js(f"window.location.href = '{XHS_LOGIN_URL}'")
            print("[xhs-leads] evaluate_js 导航已下发（双保险）")
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] evaluate_js 异常: {e!r}")
        in_login = True
        ok = False
        _t_last_import = 0.0
        _t_last_inject = 0.0
        # R-6.10 首页模式：先点开右上角「登录」面板（二维码浮于首页背景上）；失败则整页登录兜底。
        try:
            await win.evaluate_js(_OPEN_LOGIN_PANEL_JS)
        except Exception:  # noqa: BLE001（页面未就绪，下轮补注）
            pass
        _inject_login_fit()  # R-6.26c：登录页窗口适配（居中 + 滚动条）——页面未就绪则由监视循环补注
        if not cur_force:
            await _inject_active_close()
        # 小红书页内监视循环：强制锁死（离开小红书拉回首页+重开面板）；主动可关闭（URL 回 APP 即结束）。
        while not stop.is_set():
            # R-6.19：登录中 Tab1 被关 → keeper 已重建，重绑定后继续登录监视
            if await _rebind_if_closed(win, base):
                continue
            try:
                cs = await win.cookies()
            except Exception:  # noqa: BLE001
                cs = []
            has_id = any(c.get("name") == "id_token" for c in cs)
            if not cur_force:
                # ---- 主动重登：不锁死、不拉回 ----
                if time.monotonic() - _t_last_inject >= 5.0:
                    _t_last_inject = time.monotonic()
                    await _inject_active_close()
                if has_id and time.monotonic() - _t_last_import >= 0.5:
                    _t_last_import = time.monotonic()
                    r = await _post_json("/api/auth/import_cookies", {"cookies": cs})
                    if r and r.get("logged_in"):
                        print("[xhs-leads] 主动重登成功，Cookie 已注入引擎，返回 APP")
                        ok = True
                        break
                try:
                    cur = await win.current_url()
                except Exception:  # noqa: BLE001
                    cur = ""
                if cur.startswith(base):
                    print("[xhs-leads] 主动重登已关闭（回 APP，登录态不变）")
                    await _post_json("/api/auth/login_cancel", {})
                    break
            else:
                # ---- 强制登录：锁死 + 无叉 ----
                if has_id:
                    try:
                        await win.evaluate_js(_WAITING_OVERLAY_JS)
                    except Exception:  # noqa: BLE001
                        pass
                    if time.monotonic() - _t_last_import >= 0.5:
                        _t_last_import = time.monotonic()
                        r = await _post_json("/api/auth/import_cookies", {"cookies": cs})
                        if r and r.get("logged_in"):
                            print("[xhs-leads] 登录成功，Cookie 已注入引擎，返回 APP")
                            ok = True
                            break
                        print(
                            "[xhs-leads] 已捕获登录 Cookie 但引擎验证未通过:",
                            (r or {}).get("error", "未知原因（等待下次判定）"),
                        )
                        try:
                            await win.evaluate_js(_REMOVE_WAITING_OVERLAY_JS)
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    if time.monotonic() - _t_last_inject >= 10.0:
                        _t_last_inject = time.monotonic()
                        _inject_force_login()
                        _inject_login_fit()  # R-6.26c：补注登录页窗口适配（页面导航后可能丢失）
                    try:
                        cur = await win.current_url()
                    except Exception:  # noqa: BLE001
                        cur = ""
                    if cur and "/login" not in cur:
                        print(f"[xhs-leads] 离开登录页（{cur[:70]}），强制拉回登录页")
                        try:
                            await win.load_url(XHS_LOGIN_URL)
                        except Exception:  # noqa: BLE001
                            pass
                        _inject_force_login()
                        _inject_login_fit()  # R-6.26c：拉回登录页后补注窗口适配
            await asyncio.sleep(0.3)
        in_login = False
        if ok:
            # 仅真登录成功回前端（强制登录：失败/中途退出不回，保持登录页置顶）。
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
                        break  # 已切回 APP
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(1.0)


async def _rebind_if_closed(win: "ShellWindow", base: str) -> bool:
    """R-6.19：壳 tab 被 keeper 重建后，旧 page 已关闭——登录守卫重绑定新壳 page。"""
    try:
        if win.page.is_closed():
            from src import engine_pool  # noqa: PLC0415

            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            for p in ctx.pages:
                if not p.is_closed() and (p.url or "").startswith(base):
                    win.rebind(p)
                    # R-6.22：重绑定目标若是重建的新窗口，校准为左半屏布局
                    win_w, win_h, _, _ = engine_pool.shell_window_geometry()
                    await _calibrate_window(p, 0, 0, win_w, win_h)
                    print("[xhs-leads] login_guard 已重绑定重建的壳 tab", flush=True)
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


async def keep_shell_tab(base_url: str, stop: "threading.Event", on_shell_closed: "Callable[[], None] | None" = None) -> None:
    """R-6.22p 窗口监护（关窗语义重定义；承接 R-6.21 双窗口形态）。

    窗口一（Tab1，应用/登录，主窗口）：
    - **关主窗 = 关闭应用**：壳窗 close 事件 → 调用 on_shell_closed()（launcher 置位
      退出信号 + 停 uvicorn + 清理引擎），**不再即时重建**（历史 R-6.18/6.21 语义为
      防误关即时恢复，用户实测「关闭了就会重启」体验差，R-6.22p 拍板改回直觉关闭）。
    - 引用判活：登录导航（URL 变为小红书登录页）不影响存活判定。
    - 兜底轮询 5s：壳窗缺失（close 事件丢失场景）→ 同样触发 on_shell_closed 退出。
    - 启动早期（ensure_shell 尚未完成）仍允许 _rebuild_shell 兜底创建（初始块）。

    窗口二（Tab2 采集，collect_tab_visible 开关控制，独立 CFT 窗口）：
    - **关采集窗 = 仅关闭采集窗，不重建、不退出应用**（用户主动关闭即保持关闭；
      重开采集窗 = 设置页开关动作或重启应用，不再由监护自动复活）。
    - 仅启动初始（开关开）创建一次。

    兜底清理：about:blank 等空白残留 tab 随时关闭。
    """
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    base = base_url.rstrip("/")
    shell_ref: list = [None]  # 当前壳 page 对象（引用判活；登录导航 URL 变化不影响）
    _watched_pages: set = set()  # R-6.22p：已挂 close 监听的 page 集合（幂等防重挂）
    _last_urls: frozenset = frozenset()  # R-6.28：页面集变化才打印快照（防日志刷屏）

    async def _find_shell(ctx):
        # R-6.22j：shell_ref 引用判活优先——壳窗可能正被守卫导航到登录页/搜索页
        # （URL 不含 base），URL 匹配会误判「壳窗被关」触发无谓重建（用户实测
        # 「点采集后空白页/窗口消失」相关）。引用存活则直接视为壳窗存在。
        if shell_ref[0] is not None and not shell_ref[0].is_closed():
            return shell_ref[0]
        for p in ctx.pages:
            if not p.is_closed() and (p.url or "").startswith(base):
                return p
        return None

    async def _find_tab2(ctx, shell):
        # R-6.28：采集窗（Tab2）可能在 explore（采集页）或 /login-hint（未登录提示页），
        # 两者都算任务 tab——提示页期间不被当作无关残留关闭、兜底轮询可补挂监听。
        for p in ctx.pages:
            if p is shell or p.is_closed():
                continue
            u = p.url or ""
            if ("xiaohongshu.com" in u and "/login" not in u) or "login-hint" in u:
                print(
                    f"[xhs-leads] _find_tab2 命中: url={u[:60]!r} "
                    f"shell_ref_is_self={(shell is not None and p is shell)}",
                    flush=True,
                )
                return p
        return None
    def _watch(page, is_shell: bool = False) -> None:
        # R-6.22p：幂等挂载——初始块/兜底轮询可能对同一 page 重复调用，避免
        # close 时触发多次回调与重复日志（用对象引用本身去重，防 id 复用误判）。
        if any(p is page for p in _watched_pages):
            return
        _watched_pages.add(page)

        def _on_close() -> None:
            if stop.is_set():
                return
            try:
                if is_shell:
                    # R-6.22p：主窗被关 = 用户关闭应用 → 整体退出（不重建）
                    if on_shell_closed is not None:
                        print("[xhs-leads] 壳窗被关闭，触发应用退出", flush=True)
                        on_shell_closed()
                else:
                    print("[xhs-leads] 采集窗口被关闭（仅关闭采集窗，应用保持运行）", flush=True)
            except Exception:  # noqa: BLE001（loop 关闭中：兜底轮询接管）
                pass

        try:
            # R-6.22j 取证：记录壳窗 close 时的 URL，定位「壳窗被关」来源
            page.on("close", lambda: print(
                f"[xhs-leads] 壳窗 close 事件（url={page.url!r}）", flush=True))
            page.on("close", _on_close)
        except Exception:  # noqa: BLE001
            pass

    async def _rebuild_shell(ctx) -> None:
        # R-6.22c：应用模式重建——--app 命令行路由弹无菜单栏/标签栏窗口；
        # 路由不可用时退回普通 tab（能力兜底）。
        win_w, win_h, _, _ = engine_pool.shell_window_geometry()
        eng = await engine_pool.pool.get("default")
        from src import browser_client  # noqa: PLC0415

        cfts = browser_client._builtin_cft_candidates()
        page = await _launch_app_window(
            ctx, base,
            user_data_dir=eng.user_data_dir,
            executable=cfts if cfts else None,  # 返回值为单个路径 str（非列表），勿取 [0]
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
            await page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        # R-6.22：重建的新窗口默认尺寸/位置，重新校准为左半屏（主 APP 左、调试右）
        await _calibrate_window(page, 0, 0, win_w, win_h)
        shell_ref[0] = page
        _watch(page, is_shell=True)
        print("[xhs-leads] 壳窗口初始创建完成（R-6.22p 关主窗=退出应用）", flush=True)

    async def _tab2_target_url():
        """Tab2 采集窗的目标 URL：未登录 → /login-hint 提示页；已登录 → None（explore）。

        轻量判定（无副作用）：只读引擎内存 cookie，**id_token 存在 = 登录成功**（R-6.6
        权威：未登录会话不种 id_token）→ 返回 None（explore）；否则一律提示页。
        不调用 server._engine_login_state（其 has_ws 分支会 new_page 开 explore 判定页，
        与壳窗/Tab2 复用竞态、产生多余窗口）。被踢/风控因 cookie 残留可能误判已登录，
        由前端 login_status 权威链路与 ForceLogin 弹框兜底。
        """
        try:
            eng = await engine_pool.pool.get("default")
            mem = await eng.read_cookies()
            if any(c.get("name") == "id_token" for c in mem):
                return None
        except Exception:  # noqa: BLE001（引擎未就绪：按未登录处理，先给提示页）
            pass
        return f"http://127.0.0.1:{config.front_port()}/login-hint"

    async def _sweep_blank(ctx, shell) -> None:
        """清理空白残留与独立的登录残留 tab（R-6.28 启动时序优化）。

        - about:blank / 空 URL：随时关闭；
        - 独立小红书登录页残留（非壳窗、非采集窗）：关闭——避免启动时闪现
          「先登录小红书」窗口。壳窗引用（shell）无条件跳过（壳窗可能正被
          守卫导航到登录页）；explore 采集窗由 _find_tab2 复用，不在此关。
        """
        for p in list(ctx.pages):
            if p is shell or p.is_closed():
                continue
            u = (p.url or "").strip()
            if u in ("", "about:blank"):
                try:
                    await p.close()
                except Exception:  # noqa: BLE001
                    pass
            elif "xiaohongshu.com" in u and "/login" in u:
                try:
                    await p.close()
                except Exception:  # noqa: BLE001
                    pass

    # 初始：确保 Tab1 存在并挂监听；Tab2 按开关初始创建一次；清理空白残留
    while not stop.is_set():
        try:
            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            _urls = frozenset((p.url or "") for p in ctx.pages if not p.is_closed())
            if _urls != _last_urls:
                _last_urls = _urls
                print(
                    "[xhs-leads] keeper 页面集="
                    + " | ".join((p.url or "")[:45] for p in ctx.pages if not p.is_closed())
                    + "  shell_ref=" + ((shell_ref[0].url or "")[:30] if shell_ref[0] else "None"),
                    flush=True,
                )
            shell = await _find_shell(ctx)
            if shell is None:
                await _rebuild_shell(ctx)
            else:
                shell_ref[0] = shell
                _watch(shell, is_shell=True)
            # R-6.28：先清理登录/空白残留（壳窗引用跳过），避免启动闪现
            await _sweep_blank(ctx, shell_ref[0])
            # R-6.22p：采集窗仅启动初始创建一次（开关开）；用户关闭后不再自动复活
            # R-6.28：残留 explore 复用为采集窗（不新建、不闪现、不与 cleanup 竞态）；
            # 无残留才新建，新建失败（server 侧 cleanup 竞态）延迟重试 1 次。
            if config.get_config().get("collect_tab_visible"):
                async with engine_pool.TAB2_GUARD_LOCK:
                    _t2_url = await _tab2_target_url()  # R-6.28：未登录→提示页；已登录→explore
                    t2 = await _find_tab2(ctx, shell_ref[0])
                    if t2 is not None:
                        try:
                            print(
                                f"[xhs-leads] Tab2 复用前 url={t2.url[:60]!r} t2_url={_t2_url!r}",
                                flush=True,
                            )
                            if _t2_url and "login-hint" not in (t2.url or ""):
                                await t2.goto(_t2_url, wait_until="domcontentloaded", timeout=10000)
                            win_w2, win_h2, _, win_left2 = engine_pool.shell_window_geometry()
                            await _calibrate_window(t2, win_left2, 0, win_w2, win_h2)
                            _watch(t2, is_shell=False)
                        except Exception as e:  # noqa: BLE001
                            print(f"[xhs-leads] Tab2 采集窗口复用校准失败: {e!r}", flush=True)
                    else:
                        print(
                            f"[xhs-leads] Tab2 无残留，新建（t2_url={_t2_url!r}）", flush=True,
                        )
                        for _attempt in range(2):
                            try:
                                page = await eng.new_page()  # 任务页=独立采集窗口（R-6.21b 应用模式）
                                await page.goto(
                                    _t2_url or "https://www.xiaohongshu.com/explore",
                                    wait_until="domcontentloaded", timeout=10000,
                                )
                                await _ensure_debug_title(page)  # 调试窗口标题守护
                                win_w2, win_h2, _, win_left2 = engine_pool.shell_window_geometry()
                                await _calibrate_window(page, win_left2, 0, win_w2, win_h2)
                                _watch(page, is_shell=False)
                                break
                            except Exception as e:  # noqa: BLE001（创建失败：应用仍可用，仅记录）
                                if _attempt == 0:
                                    print(f"[xhs-leads] Tab2 采集窗口首次创建失败（延迟重试）: {e!r}", flush=True)
                                    await asyncio.sleep(4.0)
                                else:
                                    print(f"[xhs-leads] Tab2 采集窗口初始创建失败（可稍后经设置开关重开）: {e!r}", flush=True)
            await _sweep_blank(ctx, shell_ref[0])
            break
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                print(f"[xhs-leads] Tab1 初始化监护暂不可用（重试）: {e!r}", flush=True)
            await asyncio.sleep(2.0)

    # 兜底轮询：壳窗缺失（close 事件丢失场景）→ 触发应用退出；仅监护不重建
    while not stop.is_set():
        await asyncio.sleep(5.0)
        try:
            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            _urls = frozenset((p.url or "") for p in ctx.pages if not p.is_closed())
            if _urls != _last_urls:
                _last_urls = _urls
                print(
                    "[xhs-leads] keeper 页面集="
                    + " | ".join((p.url or "")[:45] for p in ctx.pages if not p.is_closed())
                    + "  shell_ref=" + ((shell_ref[0].url or "")[:30] if shell_ref[0] else "None"),
                    flush=True,
                )
            shell = await _find_shell(ctx)
            if shell is not None:
                shell_ref[0] = shell
                _watch(shell, is_shell=True)
            elif shell_ref[0] is None or shell_ref[0].is_closed():
                # R-6.22p：主窗已关闭 → 兜底确认退出（不再重建）
                if on_shell_closed is not None:
                    print("[xhs-leads] 兜底轮询确认主窗已关闭，触发应用退出", flush=True)
                    on_shell_closed()
                    break
            t2 = await _find_tab2(ctx, shell_ref[0])
            if t2 is not None:
                _watch(t2, is_shell=False)  # 仅补挂关闭监听（关闭=仅关采集窗）
                try:
                    # R-6.28：登录态联动——未登录 → Tab2 导航提示页；登录成功 → 回 explore。
                    # 仅当当前页与目标不一致时导航，用户手动导航别处不打断。
                    _url = await _tab2_target_url()
                    u = t2.url or ""
                    if _url and "login-hint" not in u:
                        await t2.goto(_url, wait_until="domcontentloaded", timeout=8000)
                    elif _url is None and "login-hint" in u:
                        await t2.goto(
                            "https://www.xiaohongshu.com/explore",
                            wait_until="domcontentloaded", timeout=8000,
                        )
                except Exception:  # noqa: BLE001（导航失败不阻断监护，下轮再试）
                    pass
                if _url:
                    # R-6.28：未登录时关闭孤儿 explore 页（login_status 前端轮询经
                    # _engine_login_state 可能新开判定页；未登录态无有效任务页，
                    # 多余 explore 一律判定页残留 → 关闭避免窗口堆叠）。
                    for p in list(ctx.pages):
                        if p is shell_ref[0] or p is t2 or p.is_closed():
                            continue
                        u = p.url or ""
                        if "xiaohongshu.com" in u and "/login" not in u:
                            try:
                                await p.close()
                            except Exception:  # noqa: BLE001
                                pass
            await _sweep_blank(ctx, shell_ref[0])
        except Exception as e:  # noqa: BLE001（引擎未就绪：下轮再试）
            pass
