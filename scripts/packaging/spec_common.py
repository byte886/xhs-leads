"""两个平台 PyInstaller spec 的公共定义（单一来源）。

为什么要有这个文件：macOS(.app/BUNDLE) 与 Windows(onedir/EXE) 的 spec 曾各抄一份
datas、hiddenimports 与 Analysis 参数。任何资产清单变化（例如只拷 extension/lib
而漏掉 manifest.json + background.js）都要改两处，漏一处就是"打包版功能静默缺失"：
扩展加载不上、界面少了「采集窗口」标签组，而启动日志只有一条 info，很难定位。

这里只放**两端必须一致**的东西；平台差异留在各自 spec：
- macOS：BUNDLE 外壳、icns 图标、Info.plist、引擎 post-copy 到 Contents/Resources
- Windows：EXE 的 ico 图标、额外 excludes（macOS 桥接）、引擎 post-copy 到 _internal

用法（spec 内，SPECPATH 由 PyInstaller 注入）：
    import sys
    sys.path.insert(0, SPECPATH)
    import spec_common as sc

    spec_dir, root = sc.paths(SPECPATH)
    a = Analysis(**sc.analysis_kwargs(root, spec_dir / "launcher.py"), ...)
"""
from __future__ import annotations

import pathlib

APP_NAME = "XHSLeads"

# 运行时动态导入、静态分析抓不到的第三方模块
WINDOWS_EXTRA_HIDDENIMPORTS = ["psutil", "multipart", "python_multipart"]

# 体积治理：这些在桌面端用不到，打包时统一排除
EXCLUDES_COMMON = ["tkinter", "matplotlib", "notebook"]
# Windows 上再排掉 macOS 桥接（未安装也无害；装了会显著增大体积）
EXCLUDES_WINDOWS = EXCLUDES_COMMON + ["pyobjc", "AppKit", "Quartz"]


def paths(specpath: str) -> tuple[pathlib.Path, pathlib.Path]:
    """返回 (scripts/packaging 目录, 项目根)。

    specpath 通常是 PyInstaller 注入的 SPECPATH（目录）；也接受 .spec 文件路径，
    便于在测试/工具里直接指向某个 spec。
    """
    p = pathlib.Path(specpath)
    spec_dir = p.parent if p.suffix == ".spec" or p.is_file() else p
    return spec_dir, spec_dir.parent.parent


def datas(root: pathlib.Path) -> list[tuple[str, str]]:
    """随包资产（两端完全相同）。

    - frontend/dist：后端静态托管的前端产物
    - extension：**整个目录**。manifest.json + background.js 是扩展本体（采集窗标签组
      与采集窗认领依赖），lib/extract.js 是 DOM 提取源。只拷 lib 会让
      builtin_extension_dir() 找不到 manifest.json 而静默跳过加载。
    内置引擎 CFT 不走 datas（PyInstaller 无法处理签名 app bundle 二进制），
    由 build_mac.sh / build_win.ps1 在打包后 post-copy。
    """
    return [
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "extension"), "extension"),
    ]


def hiddenimports(collect_submodules, extra: list[str] | None = None) -> list[str]:
    """src / uvicorn 全量子模块，可按平台追加。

    - collect_submodules 以参数传入，便于脱离 PyInstaller 环境做单测。
    - extra：Windows 传 WINDOWS_EXTRA_HIDDENIMPORTS；macOS 不传（其 venv 未装 psutil，
      也不需要）。
    """
    base = collect_submodules("src") + collect_submodules("uvicorn")
    return base + list(extra or [])


def analysis_kwargs(root: pathlib.Path, entry) -> dict:
    """Analysis 的两端公共参数（datas/hiddenimports/excludes 由调用方补充）。"""
    return {
        "scripts": [str(entry)],
        "pathex": [str(root / "backend")],
        "binaries": [],
        "hookspath": [],
        "hooksconfig": {},
        "runtime_hooks": [],
        "noarchive": False,
    }


def exe_kwargs(icon: str | None = None) -> dict:
    """EXE 的两端公共参数（icon 由各平台各自给）。"""
    kw = {
        "exclude_binaries": True,
        "name": APP_NAME,
        "debug": False,
        "bootloader_ignore_signals": False,
        "strip": False,
        "upx": False,
        "console": False,  # 窗口化：不弹黑框，日志由 launcher 落盘
        "disable_windowed_traceback": False,
    }
    if icon:
        kw["icon"] = str(icon)
    return kw


def collect_kwargs() -> dict:
    """COLLECT 的两端公共参数。"""
    return {"strip": False, "upx": False, "name": APP_NAME}
