#!/usr/bin/env python3
"""生产环境脚本：带参数启动 Google Chrome 开启远程调试，并打开配置页（单脚本跨平台）。

自动识别系统（macOS / Windows），按平台最佳实践定位 Chrome，带参数启动并打开
chrome://inspect/#remote-debugging 配置页——用户只需在弹出的 Chrome 里点一次「允许」，
极大减少配置难度。

用法：
    python setup_chrome.py [port] [profile_dir]
    port        调试端口，默认 9223
    profile_dir 独立 profile 目录，默认 ~/.xhs-leads-profile

退出码：
    0   已带参数启动 Chrome（配置页已打开）
    2   Chrome 已在运行（参数对已运行实例不生效，需先退出）
    3   未找到 Chrome
"""
import os
import pathlib
import subprocess
import sys


def _find_chrome() -> str | None:
    """跨平台定位 Chrome：环境变量 XHS_CHROME_PATH 优先，其次平台最佳实践。

    macOS：/Applications 默认 + ~/Applications 用户级安装。
    Windows：注册表 App Paths（HKCU→HKLM→WOW6432Node，运行对话框权威路径）→ 常见安装路径。
    """
    user = os.environ.get("XHS_CHROME_PATH")
    if user and os.path.exists(user):
        return user
    if sys.platform == "darwin":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(pathlib.Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")):
            if os.path.exists(p):
                return p
        return None
    if os.name == "nt":
        candidates = []
        try:
            import winreg
            for hive, sub in [
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            ]:
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        val, _ = winreg.QueryValueEx(k, "")
                        if val:
                            candidates.append(str(val))
                except OSError:  # noqa: PERF203
                    continue
        except ImportError:
            pass
        candidates += [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in candidates:
            if p and os.path.exists(p):
                return p
    return None


def _chrome_running() -> bool:
    """跨平台检测 Chrome 是否在运行。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                                 capture_output=True, text=True, timeout=10).stdout
            return "chrome.exe" in out
        return subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "9223"
    profile = sys.argv[2] if len(sys.argv) > 2 else str(
        pathlib.Path.home() / ".xhs-leads-profile"
    )
    chrome = _find_chrome()
    if not chrome:
        print("CHROME_NOT_FOUND")
        return 3
    os.makedirs(profile, exist_ok=True)
    if _chrome_running():
        print("CHROME_RUNNING")
        return 2
    subprocess.Popen(
        [chrome, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
         "--no-first-run", "--no-default-browser-check", "--new-window",
         "chrome://inspect/#remote-debugging"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("LAUNCHED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
