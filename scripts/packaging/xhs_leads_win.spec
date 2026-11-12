# -*- mode: python ; coding: utf-8 -*-
"""XHSLeads Windows（win64 / onedir）打包配置。

与 macOS spec 的差异：
- macOS 用 BUNDLE 出 .app；Windows 无 bundle 概念，直接 EXE + COLLECT（onedir）。
- datas 在 onedir 下落在 _MEIPASS（dist/XHSLeads/ 或 _internal/）——engine_types 的
  builtin_cft_candidates() 已按平台分支解析 chrome-win64/chrome.exe。
- 内置引擎 CFT（Windows 版 chrome-win64）同样不进 datas：build_win.ps1 在打包后
  post-copy 到 dist/XHSLeads/engine/。
- console=False：窗口化应用，日志由 launcher 落盘到 %LOCALAPPDATA%\\xhs-leads\\data\\logs\\app.log。

构建（在 Windows 上，PyInstaller 不能跨平台交叉编译）：
    python -m PyInstaller --noconfirm --clean scripts/packaging/xhs_leads_win.spec
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)          # scripts/packaging/
ROOT = SPEC_DIR.parent.parent      # 项目根
APP_NAME = "XHSLeads"

hiddenimports = (
    collect_submodules("src")
    + collect_submodules("uvicorn")
    + ["psutil", "multipart", "python_multipart"]
)
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    # 整个 extension 目录：manifest.json + background.js 是扩展本体，只拷 lib 会让
    # builtin_extension_dir() 找不到 manifest.json 而静默跳过加载（无「采集窗口」标签组）。
    (str(ROOT / "extension"), "extension"),
] + collect_data_files("playwright")

a = Analysis(
    [str(SPEC_DIR / "launcher.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "notebook", "pyobjc", "AppKit", "Quartz"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                     # 窗口化：双击不弹黑框
    disable_windowed_traceback=False,
    icon=str(ROOT / "build_resources" / "icon" / "XHSLeadsIcon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
