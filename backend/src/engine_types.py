"""engine_types — 引擎层公共资产（R-6.36c 拆环）。

承载 engine_pool / browser_client / shell_window 三方共用、且不依赖任何一方的函数与类型，
消除 engine_pool ↔ browser_client 的模块级互导环（原 browser_client._builtin_cft_candidates
被 engine_pool 模块级反向引用）。依赖方向收敛：browser_client → engine_pool → engine_types。
"""

from __future__ import annotations

import pathlib
import sys


def builtin_cft_candidates() -> str | None:
    """内置 Chrome for Testing 候选：打包态（.app 随包分发）优先，其次引擎数据目录
    旁的 CFT 副本（开发态）。R-6.11 引擎抽象层：CFT 独立身份
    （com.google.chrome.for.testing）不占用用户日常 Chrome 的 LaunchServices 单实例。
    注意 PyInstaller BUNDLE 布局差异：sys._MEIPASS = EXE 目录（Contents/MacOS），
    而 datas 落在 Contents/Resources——故两种布局都尝试（_MEIPASS/engine 与
    _MEIPASS.parent/Resources/engine）。"""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = pathlib.Path(meipass)
        candidates.append(
            str(mp / "engine/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        )
        candidates.append(
            str(mp.parent / "Resources/engine/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        )
    candidates.append(
        str(pathlib.Path.home()
            / "Library/Application Support/xhs-leads/engine/chrome-mac-x64"
            / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
    )
    for c in candidates:
        if c and pathlib.Path(c).exists():
            return c
    return None
