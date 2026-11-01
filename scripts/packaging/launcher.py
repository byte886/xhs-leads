#!/usr/bin/env python3
"""XHSLeads 应用壳入口（B 批：macOS .app；R 批：内嵌 UI 载体）。

职责：
- 确定数据目录：打包态默认 ~/Library/Application Support/xhs-leads/data
  （可被 XHS_LEADS_DATA 覆盖）；开发态沿用项目根 data。
- 同进程起 uvicorn（后台线程，优雅退出），端口冲突自动探测替换。
- 服务就绪后**内嵌 WKWebView（pywebview）呈现 UI**——打开 .app 后工具 UI
  在壳窗口内呈现、外部零 Chrome 窗口（需求变更 R 批定稿；替代旧 webbrowser.open）。
- 收到 SIGINT/SIGTERM（cmd+Q / kill）或关闭窗口时优雅停服。

运行模式（R 批）：dev=源码直跑（默认控制台日志 + vite 前端 5173）；
prod=打包进壳（默认日志落盘 data/logs/app.log + 内嵌窗口呈现静态前端）。
详见 scripts/packaging/README.md「运行模式」节。

开发态运行：  python scripts/launcher.py
打包入口：    PyInstaller .spec 指定本文件（sys._MEIPASS 分支生效）。
"""
import os
import pathlib
import signal
import sys
import threading
import time

APP_TITLE = "XHS Leads · 小红书获客助手"  # 壳窗口标题（pywebview 内嵌 UI；用户拍板品牌+中文副标题）
_WINDOW_W, _WINDOW_H = 1280, 800  # 壳窗口初始尺寸（与引擎视口一致）
# 真实登录页（R-6.5→R-6.6 强制登录）：直接导航小红书**独立整页登录页**（login?redirectPath 重定向到首页）。
# 独立登录页 = 全屏登录界面（扫码/手机号+验证码/微信），无推荐流浏览内容、无弹层叉可点；
# 登录成功后小红书自动跳转 redirectPath（首页），守卫捕获 web_session 注入引擎后回 APP。
# R-6.10（用户反馈）：登录页**保持小红书原生背景与内容**——绝不注入遮蔽类样式（历史版本
# force_login_js 的 [class*="close"] 全隐藏规则会遮蔽登录页内容致空白，已精确化，见下方）。
XHS_LOGIN_URL = (
    "https://www.xiaohongshu.com/login"
    "?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F"
)

# R-6.9 登录成功过渡提示：id_token 一出现即注入「等待中」提示，遮挡小红书登录后页面、
# 拦截用户误操作；引擎注入完成 load_url 切回 APP 时随导航消失。幂等（已有 id 不重复注入）。
# R-6.10（用户反馈）：改为**半透明**背景——不再纯白全屏遮蔽（历史版 #ffffff 把小红书页面
# 整个盖住，用户反馈「背景被遮蔽看不见」），背景透出、提示清晰，扫码完成后直接回 APP。
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


def _redirect_logs(data_dir: pathlib.Path) -> None:
    """GUI 模式（console=False）下 print/日志默认丢弃；重定向到 data/logs/app.log。"""
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    f = open(logs / "app.log", "a", encoding="utf-8")  # noqa: SIM115（进程级保持打开）
    os.dup2(f.fileno(), sys.stdout.fileno())
    os.dup2(f.fileno(), sys.stderr.fileno())


def _migrate_legacy_data(target: pathlib.Path) -> None:
    """壳数据目录改名（codex-zhuke → xhs-leads）的首次启动迁移。

    旧打包态数据 ~/Library/Application Support/codex-zhuke/data 存在而新目录不存在时，
    一次性复制（排除 logs 与私钥），保住登录态/数据库/授权不丢。
    """
    legacy = pathlib.Path.home() / "Library" / "Application Support" / "codex-zhuke" / "data"
    if not legacy.exists() or target.exists():
        return
    if any(legacy.iterdir()):
        import shutil
        # Chrome 运行锁/版本戳（SingletonLock/SingletonSocket/SingletonCookie/SingletonSignal/
        # RunningChromeVersion 等）是瞬时文件，Chrome 运行中复制必然源消失 → copytree 抛错；
        # 一并排除，且整体容错：迁移尽力而为，失败仅告警、不阻塞启动。
        _ignore = shutil.ignore_patterns(
            "logs", "license_private.pem", "*.log",
            "Singleton*", "RunningChromeVersion", "LOCK*", "lockfile",
        )
        try:
            shutil.copytree(legacy, target, ignore=_ignore)
            print(f"[xhs-leads] 已从旧数据目录迁移: {legacy}")
        except OSError as e:
            print(f"[xhs-leads] 旧数据迁移未完成（忽略，可后续手动迁移）: {e}")


def _data_dir() -> pathlib.Path:
    if os.environ.get("XHS_LEADS_DATA"):
        return pathlib.Path(os.environ["XHS_LEADS_DATA"]).expanduser()
    if getattr(sys, "_MEIPASS", None):  # PyInstaller 打包态
        return pathlib.Path.home() / "Library" / "Application Support" / "xhs-leads" / "data"
    # 开发态：项目根 data（scripts/launcher.py -> 项目根）
    return pathlib.Path(__file__).resolve().parents[2] / "data"


def main() -> int:
    data_dir = _data_dir()
    _migrate_legacy_data(data_dir)  # 旧 codex-zhuke 数据目录首次迁移（仅打包态生效）
    data_dir.mkdir(parents=True, exist_ok=True)

    # 开发态：确保 backend（src 包父目录）在 sys.path（打包态由 spec pathex 提供）
    if not getattr(sys, "_MEIPASS", None):
        backend = pathlib.Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))

    os.environ["XHS_LEADS_DATA"] = str(data_dir)  # 全局注入：server/config 统一读取
    # 注意：必须在 import config 之前注入——config 模块级 DATA_DIR 常量在 import 时定格，
    # 注入晚了会退化为 ROOT/data（打包态 = .app 内），引擎 profile/持久化 Cookie 全跑进
    # .app 包内，重打包即丢登录态（R-6.6 实测定位的顺序回归）。

    # 开发态：确保 backend（src 包父目录）在 sys.path（打包态由 spec pathex 提供）
    if not getattr(sys, "_MEIPASS", None):
        backend = pathlib.Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))

    from src import config, server  # noqa: PLC0415（先注入环境再 import）

    # 运行模式（R 批）：dev=源码直跑默认保留控制台（改码即生效、日志即时可见）；
    # prod=打包进壳默认落盘 data/logs/app.log。CZ_KEEP_CONSOLE=1/0 可显式强制。
    keep_console = os.environ.get("CZ_KEEP_CONSOLE")
    if keep_console is None:
        keep_console = "1" if config.is_dev() else "0"
    if keep_console != "1":
        _redirect_logs(data_dir)

    port = config.find_free_port(8791)
    print(f"[xhs-leads] 运行模式: {config.mode()}")
    print(f"[xhs-leads] 数据目录: {data_dir}")
    print(f"[xhs-leads] 服务端口: {port}")

    import uvicorn  # noqa: PLC0415

    srv = uvicorn.Server(
        uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="info")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    # 服务就绪：uvicorn started 信号 + HTTP 200 双确认。
    # 只等 srv.started 可能过早（startup 钩子未完成）；HTTP 200 才是真就绪。
    import urllib.request  # noqa: PLC0415

    ok = False
    for _ in range(450):  # 最多 ~45s（startup 清理/引擎冷启动可能耗时）
        if srv.started:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.0) as r:
                    if r.status == 200:
                        ok = True
                        break
            except Exception:  # noqa: BLE001（尚未就绪，继续轮询）
                pass
        time.sleep(0.1)
    if not ok:
        print("[xhs-leads] 服务启动超时，请查看日志", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(f"[xhs-leads] 已启动: {url}")
    if config.is_dev():
        print(
            "[xhs-leads] dev 模式提示: 前端热改请访问 "
            f"{config.frontend_base_url()}/（vite HMR，/api 反代到本服务）；"
            "该 URL 为本后端 API 入口。"
        )

    stop = threading.Event()

    def _on_signal(_signum, _frame):  # noqa: ANN001
        print("[xhs-leads] 收到退出信号，优雅停服…")
        srv.should_exit = True
        stop.set()

    # 信号：不阻塞任何信号（实测本环境 sigwait/sigpending 均不反映 pending，阻塞反而
    # 会让 SIGTERM 完全无响应）。注册 handler：无窗口路径主线程空闲 → 即时优雅退出；
    # GUI 路径主线程在 AppKit 主循环 → handler 延迟（已知限制，退出走关窗/cmd+Q/
    # SELF_EXIT/EXIT_ON_FILE，见 README「退出方式」）。
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    # 测试钩子（CZ_SELF_EXIT_AFTER 秒后自动触发优雅退出，供无信号环境/壳冒烟用）
    auto_exit = float(os.environ.get("CZ_SELF_EXIT_AFTER", "0") or 0)
    deadline = time.monotonic() + auto_exit if auto_exit > 0 else None

    # UI 载体（R 批定稿）：pywebview 内嵌 WKWebView 呈现前端 —— 打开 .app 后
    # 工具 UI 在壳窗口内呈现、外部零 Chrome 窗口（替代旧 webbrowser.open 系统浏览器）。
    # CZ_NO_BROWSER=1：跳过窗口（自动化冒烟/无头测试路径，语义保持兼容）。
    if os.environ.get("CZ_NO_BROWSER"):
        try:
            while not stop.wait(1.0):
                if not thread.is_alive():
                    break
                if deadline and time.monotonic() >= deadline:
                    print("[xhs-leads] 测试钩子触发优雅退出")
                    srv.should_exit = True
                    break
        except KeyboardInterrupt:
            pass
    else:
        import webview  # noqa: PLC0415（内嵌 UI 载体：macOS WKWebView）
        exit_file = os.environ.get("CZ_EXIT_ON_FILE")  # 外部退出钩子（GUI 路径信号延迟的确定性兜底）

        def _on_window_closed() -> None:
            print("[xhs-leads] 窗口已关闭，优雅停服…")
            srv.should_exit = True
            stop.set()

        def _watch_until_exit() -> None:
            """后台监控线程：信号 / 线程死亡 / 测试钩子超时 / 退出文件 → 关窗结束 GUI 循环。"""
            while not stop.wait(1.0):
                if not thread.is_alive():
                    break
                if deadline and time.monotonic() >= deadline:
                    print("[xhs-leads] 测试钩子触发优雅退出")
                    srv.should_exit = True
                    break
                if exit_file and pathlib.Path(exit_file).exists():
                    print("[xhs-leads] 退出文件出现，优雅停服…")
                    srv.should_exit = True
                    break
            try:
                window.destroy()  # 用户已关窗时抛错忽略
            except Exception:  # noqa: BLE001
                pass

        def _login_guard(window: "webview.Window", base_url: str, stop: "threading.Event") -> None:
            """登录守卫（R-6.5 真实登录页方案；R-6.6 强制登录；登录 UI 改版：启动不自动导航）。

            登录 UI 改版（用户拍板，R-6.9+）核心语义：
            - **启动不自动导航**：未登录 / 启动被踢不再直接 load_url 登录页，而是停在 APP 前端
              （前端 onMounted 检测未登录/被踢 → 显示 APP 内部**强制登录对话框**，不可关闭）。
              用户点「去登录」（login_begin force=True）→ 守卫才导航小红书真实登录页（无叉）。
            - **运行时被踢不直接导航**：轮询到 kicked → 通知 APP 前端弹强制登录对话框
              （dispatchEvent xhs-leads:kicked；前端确认后弹窗；兜底刷新 APP 触发 onMounted）。
            - **主动重登可关闭**：设置页「重新登录」（login_begin force=False）→ 登录页注入
              右上角「✕」（_ACTIVE_CLOSE_JS），点关闭回 APP，登录态不变；不锁死、不拉回。
            - **强制登录不可关闭**：force=True → 注入 force_login_js（隐藏关闭类元素）+ URL 锁死。
            - 登录成功链路不变：id_token 出现 → R-6.9 遮罩 → import_cookies 注入引擎 → 回 APP。
            CZ_SKIP_LOGIN_GUARD=1 可跳过（自动化/不想被导航时）。
            """
            if os.environ.get("CZ_SKIP_LOGIN_GUARD"):
                return
            import json  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415

            base = base_url.rstrip("/")  # 防双斜杠（base_url 形如 http://127.0.0.1:8800/）

            # 强制登录 JS（R-6.6→R-6.10）：防御性锁定登录页**明确的关闭按钮**，但**绝不遮蔽
            # 页面内容/背景**。历史版 `[class*="close"]{display:none!important}` 会隐藏一切类名
            # 含 close 的元素（登录页容器若含之即整页空白——用户反馈「背景被遮蔽看不见」），
            # 已改为精确选择器：仅隐藏 aria-label=关闭 / title=关闭 / 明确 close-btn 类。
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
                """注入/重注强制登录 JS（页面重渲染或导航后 style/observer 可能丢失，循环内周期补注）。"""
                try:
                    window.evaluate_js(force_login_js)
                except Exception as e:  # noqa: BLE001（页面未就绪等，下轮重试）
                    print(f"[xhs-leads] 强制登录 JS 注入暂未成功: {e!r}", flush=True)

            def _inject_active_close() -> None:
                """注入/重注主动重登的「✕ 返回 APP」按钮（幂等；页面重渲染后补注）。"""
                try:
                    r = window.evaluate_js(_ACTIVE_CLOSE_JS.replace("__BASE__", f"{base}/"))
                    print(f"[xhs-leads] 主动关闭按钮注入 evaluate_js -> {r!r}", flush=True)
                except Exception as e:  # noqa: BLE001（页面未就绪等，下轮重试）
                    print(f"[xhs-leads] 主动重登关闭按钮注入暂未成功: {e!r}", flush=True)

            def _get_json(path: str):
                try:
                    with urllib.request.urlopen(f"{base}{path}", timeout=3) as r:
                        return json.loads(r.read().decode("utf-8"))
                except Exception:  # noqa: BLE001（后端未就绪/网络瞬时，返回 None 继续轮询）
                    return None

            def _post_json(path: str, payload: dict):
                try:
                    req = urllib.request.Request(
                        f"{base}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return json.loads(r.read().decode("utf-8"))
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": str(e)}

            def _cookies_to_payload(cs: list) -> list[dict]:
                """pywebview get_cookies → SimpleCookie 列表 → Playwright add_cookies dict 列表。"""
                out = []
                for c in cs:
                    for key, m in c.items():
                        exp = m["expires"]
                        if hasattr(exp, "timeIntervalSince1970"):  # NSDate（cocoa 直传）
                            exp = float(exp.timeIntervalSince1970())
                        else:
                            try:
                                exp = float(exp) if exp else None
                            except (TypeError, ValueError):
                                exp = None
                        out.append({
                            "name": m.key,
                            "value": m.value,
                            "domain": m["domain"],
                            "path": m["path"],
                            "secure": str(m["secure"]).lower() == "true",
                            "httpOnly": str(m["httponly"]).lower() == "true",
                            "sameSite": (m.get("samesite") or "") or None,
                            "expires": exp,
                        })
                return out

            try:
                window.events.loaded.wait(timeout=60)  # webview.start 后窗口就绪
            except Exception:  # noqa: BLE001
                pass
            # 登录 UI 改版：启动仅做状态判定，**不自动导航登录页**——未登录/启动被踢由
            # APP 前端强制登录对话框承接（前端 onMounted 检测 → 弹窗 → 点「去登录」置 pending
            # → 守卫才导航）。守卫主职责：轮询 pending 导航登录页 + 运行时被踢通知前端。
            ls = _get_json("/api/login_status")
            kicked_at_start = bool(ls and ls.get("kicked"))
            if ls and ls.get("logged_in") and not kicked_at_start:
                print("[xhs-leads] 启动已登录——守卫进入运行时轮询（被踢检测）")
            elif kicked_at_start:
                print(
                    "[xhs-leads] 启动检测到登录已被平台踢出（登录超限/风控）"
                    "——APP 前端显示强制登录对话框（不自动导航登录页）"
                )
            else:
                print(
                    "[xhs-leads] 启动未登录——APP 前端显示强制登录对话框"
                    "（不自动导航登录页；点「去登录」后守卫导航小红书登录页）"
                )
            in_login = False
            cur_force = False
            _last_kick_notify = 0.0
            while not stop.wait(0.5):
                if in_login:
                    continue  # 正在登录页（局部轮询控制），此处等待退出
                st = _get_json("/api/auth/login_begin_state")
                pending = bool(st and st.get("pending"))
                pending_force = bool(st and st.get("force"))
                ls = _get_json("/api/login_status")
                kicked_now = bool(ls and ls.get("kicked"))
                if pending:
                    # 用户点了「去登录」（强制）或「重新登录」（主动）→ 导航小红书登录页
                    cur_force = pending_force
                    print(
                        "[xhs-leads] 切换到小红书登录页"
                        f"（{'强制登录·不可关闭' if cur_force else '主动重登·可关闭'}）"
                    )
                elif kicked_now:
                    # 运行时被踢：**不直接导航登录页**——通知 APP 前端弹强制登录对话框
                    # （前端确认 kicked 后弹窗，与启动未登录同一入口；节流 5s 防重复通知）
                    if time.monotonic() - _last_kick_notify >= 5.0:
                        _last_kick_notify = time.monotonic()
                        print(
                            "[xhs-leads] 运行时检测到登录被踢（风控/登录超限）"
                            "——通知 APP 前端弹强制登录对话框"
                        )
                        try:
                            window.evaluate_js(
                                "window.dispatchEvent(new CustomEvent('xhs-leads:kicked'))"
                            )
                        except Exception as e:  # noqa: BLE001（前端页面未就绪 → 刷新兜底）
                            print(f"[xhs-leads] 前端事件通知失败，刷新 APP 触发检测: {e!r}")
                            try:
                                window.load_url(f"{base}/")
                            except Exception:  # noqa: BLE001
                                pass
                    continue
                else:
                    continue
                # ---- 导航到小红书真实登录页（双保险） ----
                try:
                    window.load_url(XHS_LOGIN_URL)
                    print("[xhs-leads] load_url 已调用（无异常）")
                except Exception as e:  # noqa: BLE001（诊断：打包态 cocoa load_url 曾静默失效）
                    print(f"[xhs-leads] load_url 异常: {e!r}")
                import time as _t  # noqa: PLC0415
                _t.sleep(1)
                try:
                    window.evaluate_js(f"window.location.href = '{XHS_LOGIN_URL}'")
                    print("[xhs-leads] evaluate_js 导航已下发（双保险）")
                except Exception as e:  # noqa: BLE001
                    print(f"[xhs-leads] evaluate_js 异常: {e!r}")
                in_login = True
                ok = False
                import time as _t  # noqa: PLC0415
                _t_last_import = 0.0
                _t_last_inject = 0.0
                # R-6.10 首页模式：两种分支统一先点开右上角「登录」面板（二维码浮于首页背景上）；
                # 主动分支额外注入右上角「✕ 返回 APP」。
                try:
                    window.evaluate_js(_OPEN_LOGIN_PANEL_JS)
                except Exception:  # noqa: BLE001（首页未就绪，下轮补注）
                    pass
                if not cur_force:
                    _inject_active_close()
                # 小红书页内监视循环：强制锁死（离开小红书拉回首页+重开面板）；主动可关闭（URL 回 APP 即结束）。
                # 轮询 0.3s + 注入节流 0.5s：扫码成功后尽快回 APP，消除「先进小红书再回 APP」的过渡观感。
                while not stop.is_set():
                    try:
                        cs = window.get_cookies()  # 窗口当前 URL 域过滤
                    except Exception:  # noqa: BLE001
                        cs = None
                    has_id = any(
                        m.key == "id_token"
                        for c in (cs or []) for m in c.values()
                    )
                    if not cur_force:
                        # ---- 主动重登：不锁死、不拉回 ----
                        if _t.monotonic() - _t_last_inject >= 5.0:
                            _t_last_inject = _t.monotonic()
                            _inject_active_close()
                        if has_id and _t.monotonic() - _t_last_import >= 0.5:
                            _t_last_import = _t.monotonic()
                            r = _post_json("/api/auth/import_cookies", {"cookies": _cookies_to_payload(cs)})
                            if r and r.get("logged_in"):
                                print("[xhs-leads] 主动重登成功，Cookie 已注入引擎，返回 APP")
                                ok = True
                                break
                        try:
                            cur = window.get_current_url() or ""
                        except Exception:  # noqa: BLE001
                            cur = ""
                        if cur.startswith(base):
                            # 用户点「✕」回 APP（登录态不变）：结束本轮 + 清 pending
                            print("[xhs-leads] 主动重登已关闭（回 APP，登录态不变）")
                            _post_json("/api/auth/login_cancel", {})
                            break
                    else:
                        # ---- 强制登录：锁死 + 无叉 ----
                        if has_id:
                            # R-6.9 过渡遮罩：真登录态一现立即注入「等待中」，遮挡小红书登录后
                            # 页面、拦截误操作（幂等；页面若在跳转导致遮罩丢失，下轮自动补注）。
                            try:
                                window.evaluate_js(_WAITING_OVERLAY_JS)
                            except Exception:  # noqa: BLE001（页面未就绪等，下轮补注）
                                pass
                            # 真登录态已现：快速注入引擎（节流 0.5s——id_token 是真实登录标志，
                            # 无占位误触风险；重复注入幂等，仅引擎 reload 轻量）。R-6.10 提速：
                            # 扫码成功到回 APP 应尽量短，避免用户看到小红书页面跳转。
                            if _t.monotonic() - _t_last_import >= 0.5:
                                _t_last_import = _t.monotonic()
                                r = _post_json("/api/auth/import_cookies", {"cookies": _cookies_to_payload(cs)})
                                if r and r.get("logged_in"):
                                    print("[xhs-leads] 登录成功，Cookie 已注入引擎，返回 APP")
                                    ok = True
                                    break
                                print(
                                    "[xhs-leads] 已捕获登录 Cookie 但引擎验证未通过:",
                                    (r or {}).get("error", "未知原因（等待下次判定）"),
                                )
                                # R-6.9：验证未通过 → 移除遮罩，让用户重新看到登录页重试
                                try:
                                    window.evaluate_js(_REMOVE_WAITING_OVERLAY_JS)
                                except Exception:  # noqa: BLE001
                                    pass
                        else:
                            # 未登录（无 id_token）：必须停在整页登录页——URL 锁死 + 周期补注 JS
                            if _t.monotonic() - _t_last_inject >= 10.0:
                                _t_last_inject = _t.monotonic()
                                _inject_force_login()
                            try:
                                cur = window.get_current_url() or ""
                            except Exception:  # noqa: BLE001
                                cur = ""
                            if cur and "/login" not in cur:
                                print(f"[xhs-leads] 离开登录页（{cur[:70]}），强制拉回登录页")
                                try:
                                    window.load_url(XHS_LOGIN_URL)
                                except Exception:  # noqa: BLE001
                                    pass
                                _inject_force_login()
                    stop.wait(0.3)  # R-6.10：0.3s 轮询——扫码成功后更快捕获并回 APP（无小红书过渡）
                in_login = False
                if ok:
                    # 仅真登录成功回前端（强制登录：失败/中途退出不回，保持登录页置顶）。
                    # R-6.6 提速：load_url + evaluate_js 双保险（打包态 cocoa load_url 曾
                    # 静默失效——历史「登录成功却回不去」的最后一跳隐患），确保一次切回。
                    for _attempt in range(3):
                        try:
                            window.load_url(f"{base}/")
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            window.evaluate_js("window.location.href = '%s'" % f"{base}/")
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            cur = window.get_current_url() or ""
                            if cur.startswith(base):
                                break  # 已切回 APP
                        except Exception:  # noqa: BLE001
                            pass
                        stop.wait(1.0)

        window = webview.create_window(
            APP_TITLE, url, width=_WINDOW_W, height=_WINDOW_H, min_size=(1024, 700)
        )
        window.events.closed += _on_window_closed
        threading.Thread(target=_watch_until_exit, daemon=True).start()
        threading.Thread(target=_login_guard, args=(window, url, stop), daemon=True).start()
        webview.start()
        # webview.start 返回 = 全部窗口已关闭 → 优雅停服

    srv.should_exit = True
    thread.join(timeout=8)
    print("[xhs-leads] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
