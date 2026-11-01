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
import asyncio


def _http_probe(port: int, timeout: float = 1.0) -> bool:
    """线程内同步 HTTP 探测（仅 _wait_ready 使用；不阻塞事件循环）。"""
    import urllib.request  # noqa: PLC0415

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False
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
    # R-6.17：实际服务端口经环境变量传给 engine_pool（get_config() 每次返回新 dict，不可跨模块写）
    os.environ["XHS_SHELL_URL"] = f"http://127.0.0.1:{port}/"
    print(f"[xhs-leads] 运行模式: {config.mode()}")
    print(f"[xhs-leads] 数据目录: {data_dir}")
    print(f"[xhs-leads] 服务端口: {port}")

    import uvicorn  # noqa: PLC0415
    import asyncio  # noqa: PLC0415

    # R-6.16 路线 A（关键修复）：uvicorn 与壳协程运行在**同一事件循环**。
    # 背景：壳窗口 = CFT 引擎窗口，ensure_shell 若在独立线程的独立 loop 里抢先创建
    # engine_pool 全局 playwright 实例，后端 /api/login_status 的 CDP 探测将跨 loop
    # 使用该实例 → 挂起卡死（login_status 超时、守卫轮询堆积）。单 loop 根治。
    url = f"http://127.0.0.1:{port}/"

    srv = uvicorn.Server(
        uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="info")
    )
    stop = threading.Event()

    def _on_signal(_signum, _frame):  # noqa: ANN001
        print("[xhs-leads] 收到退出信号，优雅停服…")
        srv.should_exit = True
        stop.set()

    # 信号：不阻塞任何信号（实测本环境 sigwait/sigpending 均不反映 pending，阻塞反而
    # 会让 SIGTERM 完全无响应）。注册 handler：serve() 每 tick 检查 should_exit 即退出。
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    # 测试钩子（CZ_SELF_EXIT_AFTER 秒后自动触发优雅退出，供无信号环境/壳冒烟用）
    auto_exit = float(os.environ.get("CZ_SELF_EXIT_AFTER", "0") or 0)
    deadline = time.monotonic() + auto_exit if auto_exit > 0 else None
    exit_file = os.environ.get("CZ_EXIT_ON_FILE")  # 外部退出钩子（确定性兜底）

    async def _wait_ready() -> bool:
        """服务就绪：uvicorn started 信号 + HTTP 200 双确认。

        R-6.16 关键修复：单事件循环下，同步 urllib 轮询会阻塞整个 loop
        （uvicorn startup 被饿死 → 8791 无响应 → 与 shell 等待互相死锁）。
        HTTP 探测一律 asyncio.to_thread 脱离 loop。
        """
        for _ in range(450):  # 最多 ~45s（startup 清理/引擎冷启动可能耗时）
            if srv.started:
                try:
                    ok = await asyncio.to_thread(_http_probe, port)
                    if ok:
                        return True
                except Exception:  # noqa: BLE001（尚未就绪，继续轮询）
                    pass
            await asyncio.sleep(0.1)
        return False

    async def _shell_main_async(url: str) -> None:
        """壳主流程：与 uvicorn 同一事件循环（CFT 引擎窗口承载前端 + 登录守卫）。

        R-6.18：壳窗口双 Tab——Tab1（应用/登录）固定不可关（keep_shell_tab 监护，
        关闭即自动恢复）；Tab2（采集）由设置页 collect_tab_visible 开关控制显示。
        """
        # 先等后端 HTTP 就绪：防止 8791 尚未监听时 goto 被拒 / 守卫误判未登录强制登录
        await _wait_ready()
        from src import shell_window  # noqa: PLC0415

        try:
            win = await shell_window.ensure_shell(url)
            print(f"[xhs-leads] 壳窗口就绪（CFT 承载前端，CDP 9223 可控）: {url}")
        except Exception as e:  # noqa: BLE001（引擎拉起失败：服务仍可用，仅打日志）
            print(f"[xhs-leads] 壳窗口启动异常（服务仍可用）: {e!r}", file=sys.stderr)
            return
        guard = asyncio.create_task(shell_window.login_guard(win, url, stop))
        keeper = asyncio.create_task(shell_window.keep_shell_tab(url, stop))
        try:
            await asyncio.gather(guard, keeper)
        except asyncio.CancelledError:  # noqa: PERF203（服务停止时随 _amain 取消）
            pass
        except Exception as e:  # noqa: BLE001（守卫异常不应拖垮服务）
            print(f"[xhs-leads] 登录守卫退出: {e!r}", flush=True)

    async def _watch_async() -> None:
        """监控协程：测试钩子超时 / 退出文件 → 优雅停服。"""
        while True:
            await asyncio.sleep(1.0)
            if deadline and time.monotonic() >= deadline:
                print("[xhs-leads] 测试钩子触发优雅退出")
                srv.should_exit = True
                return
            if exit_file and pathlib.Path(exit_file).exists():
                print("[xhs-leads] 退出文件出现，优雅停服…")
                srv.should_exit = True
                return

    async def _amain(url: str) -> None:
        tasks = []
        # 必须先启动 serve 再轮询就绪：否则 _wait_ready 等待期间服务从未监听，
        # 45s 必超时且 should_exit 置位后 serve 一启动即退出（实测顺序回归）。
        serve_task = asyncio.create_task(srv.serve())
        if not os.environ.get("CZ_NO_BROWSER"):
            tasks.append(asyncio.create_task(_shell_main_async(url)))
        watcher = asyncio.create_task(_watch_async())
        ready = await _wait_ready()
        if not ready:
            print("[xhs-leads] 服务启动超时，请查看日志", file=sys.stderr)
            srv.should_exit = True
        else:
            print(f"[xhs-leads] 已启动: {url}")
            if config.is_dev():
                print(
                    "[xhs-leads] dev 模式提示: 前端热改请访问 "
                    f"{config.frontend_base_url()}/（vite HMR，/api 反代到本服务）；"
                    "该 URL 为本后端 API 入口。"
                )
        await serve_task  # 返回 = 服务已停止（should_exit 被信号/监控置位）
        for t in tasks:
            t.cancel()
        watcher.cancel()

    asyncio.run(_amain(url))
    print("[xhs-leads] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
