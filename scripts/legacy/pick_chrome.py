#!/usr/bin/env python3
"""生产环境脚本：弹出系统文件对话框，让用户指定 Chrome（未找到可用 Chrome 时使用）。

跨平台：macOS 用 osascript 原生「选取文件」对话框（选 Google Chrome.app）；
Windows 用 tkinter 文件对话框（选 chrome.exe）。输出所选 Chrome 可执行文件绝对路径
（未选择/取消则输出空行）。

用法：python pick_chrome.py
"""
import os
import subprocess
import sys


def _pick_macos() -> str:
    """macOS：osascript 原生对话框选 .app，转 POSIX 路径后拼可执行文件。"""
    script = (
        'set f to choose file with prompt "请选择 Google Chrome 应用程序" of type {"app"}\n'
        "return POSIX path of f"
    )
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=300).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""
    if not out:
        return ""
    exe = os.path.join(out, "Contents", "MacOS", "Google Chrome")
    return exe if os.path.exists(exe) else ""


def _pick_windows() -> str:
    """Windows：tkinter 文件对话框选 chrome.exe。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        p = filedialog.askopenfilename(
            title="请选择 Google Chrome（chrome.exe）",
            filetypes=[("Chrome 可执行文件", "*.exe")],
        )
        root.destroy()
        return p or ""
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    picked = _pick_macos() if sys.platform == "darwin" else (
        _pick_windows() if os.name == "nt" else "")
    print(picked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
