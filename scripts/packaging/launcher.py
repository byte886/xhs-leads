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

APP_TITLE = "XHS Leads"  # 壳窗口标题（pywebview 内嵌 UI）
_WINDOW_W, _WINDOW_H = 1280, 800  # 壳窗口初始尺寸（与引擎视口一致）


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

    from src import config, server  # noqa: PLC0415（先注入环境再 import）

    # 运行模式（R 批）：dev=源码直跑默认保留控制台（改码即生效、日志即时可见）；
    # prod=打包进壳默认落盘 data/logs/app.log。CZ_KEEP_CONSOLE=1/0 可显式强制。
    keep_console = os.environ.get("CZ_KEEP_CONSOLE")
    if keep_console is None:
        keep_console = "1" if config.is_dev() else "0"
    if keep_console != "1":
        _redirect_logs(data_dir)
    os.environ["XHS_LEADS_DATA"] = str(data_dir)  # 全局注入：server/config 统一读取

    # 开发态：确保 backend（src 包父目录）在 sys.path
    if not getattr(sys, "_MEIPASS", None):
        backend = pathlib.Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))

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

        window = webview.create_window(
            APP_TITLE, url, width=_WINDOW_W, height=_WINDOW_H, min_size=(1024, 700)
        )
        window.events.closed += _on_window_closed
        threading.Thread(target=_watch_until_exit, daemon=True).start()
        webview.start()
        # webview.start 返回 = 全部窗口已关闭 → 优雅停服

    srv.should_exit = True
    thread.join(timeout=8)
    print("[xhs-leads] 已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
