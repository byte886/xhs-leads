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

    def _inject_force_login() -> None:
        try:
            win.evaluate_js(force_login_js)
        except Exception as e:  # noqa: BLE001
            print(f"[xhs-leads] 强制登录 JS 注入暂未成功: {e!r}", flush=True)

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
        print("[xhs-leads] 启动已登录——守卫进入运行时轮询（被踢检测）")
    elif kicked_at_start:
        print("[xhs-leads] 启动检测到登录已被平台踢出——APP 前端显示强制登录对话框")
    else:
        print("[xhs-leads] 启动未登录——APP 前端显示强制登录对话框")

    in_login = False
    cur_force = False
    _last_kick_notify = 0.0
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
        ls = await _get_json("/api/login_status")
        kicked_now = bool(ls and ls.get("kicked"))
        if pending:
            cur_force = pending_force
            print(
                "[xhs-leads] 切换到小红书登录页"
                f"（{'强制登录·不可关闭' if cur_force else '主动重登·可关闭'}）"
            )
        elif kicked_now:
            if time.monotonic() - _last_kick_notify >= 5.0:
                _last_kick_notify = time.monotonic()
                print("[xhs-leads] 运行时检测到登录被踢——通知 APP 前端弹强制登录对话框")
                try:
                    await win.evaluate_js(
                        "window.dispatchEvent(new CustomEvent('xhs-leads:kicked'))"
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"[xhs-leads] 前端事件通知失败，刷新 APP 触发检测: {e!r}")
                    try:
                        await win.load_url(f"{base}/")
                    except Exception:  # noqa: BLE001
                        pass
            continue
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
                    print("[xhs-leads] login_guard 已重绑定重建的壳 tab", flush=True)
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


async def keep_shell_tab(base_url: str, stop: "threading.Event") -> None:
    """R-6.19 Tab1/Tab2 监护（事件驱动即时恢复，替代 R-6.18 的 2s 轮询）。

    Tab1（应用/登录，固定不可关）：
    - 持有壳 page 引用，close 事件触发**即时重建**（goto 前端 + bring_to_front），
      用户感知 ≈ 关不掉；引用判活——登录导航（URL 变为小红书登录页）不影响存活判定。
    - 兜底轮询 5s（事件丢失 / 引擎重建场景）。
    Tab2（采集，collect_tab_visible 开关控制）：
    - 开关开：小红书采集 tab 被关闭 → 重建 xiaohongshu 骨架并置前（Tab2 常驻语义，
      「只能由开关关闭」）；开关关：不守护、不创建（空闲采集 tab 由 /api/app_config 联动关闭）。
    兜底清理：about:blank 等空白残留 tab（引擎启动默认页如未被复用）随时关闭。
    """
    from src import config, engine_pool  # noqa: PLC0415（launcher 注入环境后导入）

    base = base_url.rstrip("/")
    shell_ref: list = [None]  # 当前壳 page 对象（引用判活；登录导航 URL 变化不影响）
    _recovering: list = [False]  # 防重入：close 事件可能对同一销毁派发多次
    _last_recover: list = [0.0]  # 时间窗：迟到重发事件（1s 内）直接忽略

    async def _find_shell(ctx):
        for p in ctx.pages:
            if not p.is_closed() and (p.url or "").startswith(base):
                return p
        return None

    async def _find_tab2(ctx, shell):
        for p in ctx.pages:
            if p is shell or p.is_closed():
                continue
            u = p.url or ""
            if "xiaohongshu.com" in u and "/login" not in u:
                return p
        return None

    def _watch(page) -> None:
        def _on_close() -> None:
            if stop.is_set():
                return
            try:
                asyncio.get_running_loop().create_task(_recover())
            except Exception:  # noqa: BLE001（loop 关闭中：兜底轮询接管）
                pass

        try:
            page.on("close", _on_close)
        except Exception:  # noqa: BLE001
            pass

    async def _rebuild_shell(ctx) -> None:
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
        shell_ref[0] = page
        _watch(page)
        print("[xhs-leads] 壳 Tab1 被关闭，已即时恢复前端 tab（R-6.19 事件驱动）", flush=True)

    async def _recover() -> None:
        """close 事件处理器：Tab1 / Tab2 被关，按开关状态即时恢复（防重入+时间窗）。"""
        now = time.monotonic()
        if stop.is_set() or _recovering[0] or (now - _last_recover[0]) < 1.0:
            return
        _last_recover[0] = now
        _recovering[0] = True
        try:
            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            shell = await _find_shell(ctx)
            if shell is not None:
                shell_ref[0] = shell
            elif shell_ref[0] is None or shell_ref[0].is_closed():
                await _rebuild_shell(ctx)
            if config.get_config().get("collect_tab_visible"):
                async with engine_pool.TAB2_GUARD_LOCK:
                    if await _find_tab2(ctx, shell_ref[0]) is None:
                        page = await ctx.new_page()
                        try:
                            await page.goto(
                                "https://www.xiaohongshu.com/explore",
                                wait_until="domcontentloaded", timeout=10000,
                            )
                        except Exception:  # noqa: BLE001
                            try:
                                await page.close()
                            except Exception:  # noqa: BLE001
                                pass
                        else:
                            _watch(page)  # Tab2 同样事件监护（被关即时重建，R-6.19）
                            try:
                                await page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                            print("[xhs-leads] Tab2 被关闭，已按开关重建采集骨架（R-6.19）", flush=True)
            # 重建后清理残留空白页（goto 失败页 / 并发残留）
            await _sweep_blank(ctx, shell_ref[0])
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                print(f"[xhs-leads] Tab 监护恢复暂不可用（兜底轮询接管）: {e!r}", flush=True)
        finally:
            _recovering[0] = False

    async def _sweep_blank(ctx, shell) -> None:
        for p in list(ctx.pages):
            if p is shell or p.is_closed():
                continue
            if (p.url or "").strip() in ("", "about:blank"):
                try:
                    await p.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _sweep_tab2_dup(ctx, shell) -> None:
        """兜底收敛：并发双建时多余 explore 骨架只保留一个（绝不触碰采集结果页）。"""
        t2s = [
            p for p in ctx.pages
            if p is not shell and not p.is_closed()
            and (p.url or "").startswith("https://www.xiaohongshu.com/explore")
        ]
        if len(t2s) > 1:
            for p in t2s[1:]:
                try:
                    await p.close()
                except Exception:  # noqa: BLE001
                    pass
            print(f"[xhs-leads] 收敛重复 Tab2 骨架（{len(t2s)} → 1）", flush=True)

    # 初始：确保 Tab1 存在并挂监听；顺带清理空白残留
    while not stop.is_set():
        try:
            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            shell = await _find_shell(ctx)
            if shell is None:
                await _rebuild_shell(ctx)
            else:
                shell_ref[0] = shell
                _watch(shell)
            await _sweep_blank(ctx, shell_ref[0])
            break
        except Exception as e:  # noqa: BLE001
            if not stop.is_set():
                print(f"[xhs-leads] Tab1 初始化监护暂不可用（重试）: {e!r}", flush=True)
            await asyncio.sleep(2.0)

    # 兜底轮询：事件丢失 / 引擎重建场景下仍恢复 Tab1、守护 Tab2、清理空白
    while not stop.is_set():
        await asyncio.sleep(5.0)
        try:
            eng = await engine_pool.pool.get("default")
            ctx = await eng.context()
            shell = await _find_shell(ctx)
            if shell is not None:
                shell_ref[0] = shell
                _watch(shell)
            elif shell_ref[0] is None or shell_ref[0].is_closed():
                await _rebuild_shell(ctx)
            t2 = await _find_tab2(ctx, shell_ref[0])
            if t2 is not None:
                _watch(t2)  # 无论谁创建的 Tab2（server 开关联动/引擎），轮询补挂事件监护
            elif config.get_config().get("collect_tab_visible"):
                async with engine_pool.TAB2_GUARD_LOCK:
                    if await _find_tab2(ctx, shell_ref[0]) is None:
                        page = await ctx.new_page()
                        try:
                            await page.goto(
                                "https://www.xiaohongshu.com/explore",
                                wait_until="domcontentloaded", timeout=10000,
                            )
                        except Exception:  # noqa: BLE001
                            try:
                                await page.close()
                            except Exception:  # noqa: BLE001
                                pass
                        else:
                            _watch(page)  # Tab2 兜底创建同样挂事件监护（R-6.19）
                            try:
                                await page.bring_to_front()
                            except Exception:  # noqa: BLE001
                                pass
                            print("[xhs-leads] Tab2 兜底重建（开关开）", flush=True)
            await _sweep_blank(ctx, shell_ref[0])
            await _sweep_tab2_dup(ctx, shell_ref[0])  # 兜底收敛：并发双建的多余 explore 骨架
        except Exception:  # noqa: BLE001（引擎未就绪：下轮再试）
            pass
