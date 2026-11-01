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
XHS_LOGIN_URL = (
    "https://www.xiaohongshu.com/login"
    "?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F"
)

# R-6.9 登录成功过渡遮罩：id_token 一出现即注入全屏「等待中」，遮挡小红书登录后页面、
# 拦截用户误操作；引擎注入完成 load_url 切回 APP 时随导航消失。幂等（已有 id 不重复注入）。
_WAITING_OVERLAY_JS = r"""(() => {
  if (document.getElementById('xhs-leads-waiting')) return;
  const d = document.createElement('div');
  d.id = 'xhs-leads-waiting';
  d.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:#ffffff;'
    + 'z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;';
  const st = document.createElement('style');
  st.textContent = '@keyframes xhs-leads-spin{to{transform:rotate(360deg)}}';
  const t = document.createElement('div');
  t.textContent = '登录成功，正在进入工作台…';
  t.style.cssText = 'font-size:18px;color:#333;margin-bottom:16px;letter-spacing:1px;';
  const s = document.createElement('div');
  s.style.cssText = 'width:26px;height:26px;border:3px solid #ff2442;border-top-color:transparent;'
    + 'border-radius:50%;animation:xhs-leads-spin 1s linear infinite;';
  d.appendChild(st); d.appendChild(t); d.appendChild(s);
  document.body.appendChild(d);
})()"""
_REMOVE_WAITING_OVERLAY_JS = (
    "(() => { const e = document.getElementById('xhs-leads-waiting'); if (e) e.remove(); })()"
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
            """登录守卫（R-6.5 真实登录页方案；R-6.6 强制登录）。

            链路：服务就绪即主动 login_begin（未登录置 pending，无需用户点击）→ 守卫
            轮询到 pending → 窗口 load_url(小红书真实登录页)（用户在壳窗口内原生完成扫码 /
            手机号+获取验证码，验证码真实走「获取验证码」流程）→ 注入强制登录 JS
            （隐藏登录弹层关闭按钮，用户无法跳过）→ 轮询 get_cookies() 出现 web_session
            （HttpOnly 亦可得）→ POST /api/auth/import_cookies 注入无头引擎独立 profile
            （幂等）→ 引擎真实判定登录 → 窗口回 APP 前端。
            强制语义（用户拍板）：未登录时窗口永远停在登录页（无「稍后再说/关闭」路径），
            注入未通过/URL 漂移均留在登录页；仅登录成功才回 APP，无超时回退。
            CZ_SKIP_LOGIN_GUARD=1 可跳过（自动化/不想被导航时）。
            """
            if os.environ.get("CZ_SKIP_LOGIN_GUARD"):
                return
            import json  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415

            base = base_url.rstrip("/")  # 防双斜杠（base_url 形如 http://127.0.0.1:8800/）

            # 强制登录 JS（R-6.6）：防御性锁定——登录页若渲染出任何 close/关闭类元素一律隐藏
            # （整页登录页本身无可点浏览内容；弹层模式遗留的 MutationObserver 保留兜底）。
            force_login_js = (
                "(function(){"
                "if(window.__xhs_leads_lock_ok)return;window.__xhs_leads_lock_ok=true;"
                "var st=document.createElement('style');st.id='xhs-leads-lock';"
                "st.textContent='[class*=\"close\"]{display:none!important}.close{display:none!important}';"
                "document.head.appendChild(st);"
                "new MutationObserver(function(){"
                "var a=document.querySelectorAll('[class*=\"close\"],[class*=\"icon-close\"]');"
                "for(var i=0;i<a.length;i++){a[i].style.display='none';a[i].removeAttribute('onclick');}"
                "}).observe(document.body,{childList:true,subtree:true});})();"
            )

            def _inject_force_login() -> None:
                """注入/重注强制登录 JS（页面重渲染或导航后 style/observer 可能丢失，循环内周期补注）。"""
                try:
                    window.evaluate_js(force_login_js)
                except Exception as e:  # noqa: BLE001（页面未就绪等，下轮重试）
                    print(f"[xhs-leads] 强制登录 JS 注入暂未成功: {e!r}")

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
            # 启动即强制（R-6.6）：服务就绪主动置登录态检查，未登录直接进入登录页导航流程，
            # 不再依赖前端「前往登录」按钮（实测打包态按钮点击偶发不触发后端请求）。
            # R-6.8 被踢统一：login_begin 对「kicked」（平台登录超限/请重新登录，cookie 残留
            # 假阳性）不再短路，kicked 视为未登录 → 启动即进登录页；运行时被踢同样自动拉回。
            r0 = _post_json("/api/auth/login_begin", {})
            ls = _get_json("/api/login_status")
            kicked_at_start = bool(ls and ls.get("kicked"))
            if r0 and r0.get("already_logged_in") and not kicked_at_start:
                return  # 已登录且未被踢：无需守卫
            # R-6.6 提速：login_begin 依赖 30s 缓存，重启后缓存空会误报未登录 → 导航登录页。
            # 引擎实时判定兜底（login_status 会走引擎 CDP 真实判定）：已登录则秒回 APP 首页。
            if ls and ls.get("logged_in") and not kicked_at_start:
                print("[xhs-leads] 引擎已登录，跳过登录页")
                return
            if kicked_at_start:
                print("[xhs-leads] 检测到登录已被平台踢出（登录超限/风控），启动即进入登录页")
            if not r0 or not r0.get("ok"):
                print(f"[xhs-leads] login_begin 未就绪: {(r0 or {}).get('error', '重试')}")
            in_login = False
            while not stop.wait(0.5):
                if in_login:
                    continue  # 正在登录页（局部轮询控制），此处等待退出
                # R-6.8 运行时被踢轮询：登录态被平台踢出（风控/登录超限）→ 自动回登录页，
                # 不依赖前端操作或 pending（采集 403 与登录页导航从此同源驱动）。
                ls = _get_json("/api/login_status")
                kicked_now = bool(ls and ls.get("kicked"))
                st = _get_json("/api/auth/login_begin_state")
                if not kicked_now and (not st or not st.get("pending")):
                    continue
                if kicked_now:
                    print("[xhs-leads] 运行时检测到登录被踢（风控/登录超限），自动切回登录页")
                print("[xhs-leads] 切换到小红书登录页（壳窗口内真实页面，扫码/手机号+验证码原生操作）")
                try:
                    window.load_url(XHS_LOGIN_URL)
                    print("[xhs-leads] load_url 已调用（无异常）")
                except Exception as e:  # noqa: BLE001（诊断：打包态 cocoa load_url 曾静默失效）
                    print(f"[xhs-leads] load_url 异常: {e!r}")
                # 双保险：load_url 若因打包态 callAfter/loadRequest 静默失效，改由页面内 JS 导航兜底
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
                # 强制语义（R-6.6）：无超时——未登录期间窗口永远停留在小红书整页登录页；
                # 仅 stop（退出）或登录成功才离开。整页登录页无浏览内容，URL 漂移即拉回。
                while not stop.is_set():
                    # 只认 id_token（R-6.6 修复）：web_session 未登录态也会种占位值，
                    # 以它为触发会注入无效会话 + 与 URL 锁死互相打架（拉回 login 会覆盖
                    # 壳窗口真实会话 cookie）。id_token 是登录成功专属令牌（未登录不种），
                    # 它出现 = 用户已真实登录，才值得注入引擎。
                    try:
                        cs = window.get_cookies()  # 窗口当前 URL 域过滤
                    except Exception:  # noqa: BLE001
                        cs = None
                    has_id = any(
                        m.key == "id_token"
                        for c in (cs or []) for m in c.values()
                    )
                    if has_id:
                        # R-6.9 过渡遮罩：真登录态一现立即注入「等待中」，遮挡小红书登录后
                        # 页面、拦截误操作（幂等；页面若在跳转导致遮罩丢失，下轮自动补注）。
                        try:
                            window.evaluate_js(_WAITING_OVERLAY_JS)
                        except Exception:  # noqa: BLE001（页面未就绪等，下轮补注）
                            pass
                        # 真登录态已现：快速注入引擎（R-6.6 提速：节流 2s——id_token 是
                        # 真实登录标志，无占位误触风险；重复注入幂等，仅引擎 reload 轻量）。
                        # 注入成功后 break 回 APP；未通过（极少见）也不拉回，等待下次判定。
                        if _t.monotonic() - _t_last_import >= 2.0:
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
                        # 节流窗口内：不拉回（登录态已现，放行等注入判定）
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
                    stop.wait(1.0)
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
