"""engine_types — 引擎层公共资产。

承载 engine_pool / browser_client / shell_window 三方共用、且不依赖任何一方的函数与类型，
消除 engine_pool ↔ browser_client 的模块级互导环（原 browser_client._builtin_cft_candidates
被 engine_pool 模块级反向引用）。依赖方向收敛：browser_client → engine_pool → engine_types。
"""

from __future__ import annotations

import hashlib
import pathlib
import sys


def builtin_cft_candidates() -> str | None:
    """内置 Chrome for Testing 候选：打包态（.app 随包分发）优先，其次引擎数据目录
    旁的 CFT 副本（开发态）。引擎抽象层：CFT 独立身份
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


# ---------------------------------------------------------------------------
# 与 CFT 同为随应用分发的资产，故候选解析风格一致（打包态优先、开发态兜底）。
# ---------------------------------------------------------------------------
EXTENSION_DIR_NAME = "extension"


def builtin_extension_candidates() -> list[str]:
    """扩展目录候选：打包态（随包 Resources）优先，其次项目根 extension（开发态）。

    PyInstaller 布局：macOS datas 落在 Contents/Resources（_MEIPASS.parent/Resources），
    Windows/onedir 的 datas 直接在 _MEIPASS 下——故两种打包路径都尝试，跨平台一致。
    """
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = pathlib.Path(meipass)
        candidates.append(str(mp / EXTENSION_DIR_NAME))
        candidates.append(str(mp.parent / "Resources" / EXTENSION_DIR_NAME))
    # 开发态：backend/src/engine_types.py 的 parents[2] = 项目根。
    candidates.append(
        str(pathlib.Path(__file__).resolve().parents[2] / EXTENSION_DIR_NAME)
    )
    return candidates


def builtin_extension_dir() -> str | None:
    """返回第一个含 manifest.json 的扩展目录；无则 None（不追加 flag，行为不变）。"""
    for c in builtin_extension_candidates():
        if c and (pathlib.Path(c) / "manifest.json").exists():
            return c
    return None


def unpacked_extension_id(ext_dir: str) -> str:
    """未打包扩展的扩展 ID。

    Chromium 对 `--load-extension` 的目录型扩展，ID 由**绝对路径**的 SHA256 派生：
    取前 16 字节，每个 nibble 映射为 'a'+n。实测与运行时
    `chrome-extension://<id>/background.js` 的 ID 完全一致，所以不需要再问浏览器。
    """
    digest = hashlib.sha256(str(pathlib.Path(ext_dir).resolve()).encode()).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)
