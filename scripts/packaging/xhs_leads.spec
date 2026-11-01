# -*- mode: python ; coding: utf-8 -*-
"""XHSLeads macOS .app 打包配置（B 批壳）。

要点：
- 入口 scripts/launcher.py（起 uvicorn + 开浏览器 + 优雅退出）。
- pathex 含 backend（src 包父目录），使 `src.*` 可被收集。
- datas 打包前端构建产物 frontend/dist（server 静态托管）+ playwright driver（引擎 node 子进程）。
- hiddenimports 全量收集 src / uvicorn 子模块，避免动态导入漏包。
- console=False：GUI 应用；launcher 已将 stdout/stderr 重定向到数据目录 logs/app.log。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)           # scripts/packaging/
ROOT = SPEC_DIR.parent.parent          # 项目根
APP_NAME = "XHSLeads"

hiddenimports = (
    collect_submodules("src")
    + collect_submodules("uvicorn")
    + collect_submodules("webview")  # R 批：内嵌 UI 载体（pywebview，macOS cocoa 后端）
)
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "extension" / "lib"), "extension/lib"),          # DOM 提取源 extract.js
    (str(ROOT / "scripts" / "browser"), "scripts/browser"),      # 授权代点 AppleScript
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
    excludes=["tkinter", "matplotlib", "notebook"],
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
    console=False,  # GUI 模式：日志由 launcher 落盘
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,
    bundle_identifier="com.xhsleads.app",
    info_plist={
        # 品牌名统一为「XHS Leads」（带空格）：窗口标题/前端已是，Dock/菜单栏/Finder
        # 由 CFBundleName+CFBundleDisplayName 决定（原为连写 XHSLeads，用户反馈标题不对）。
        "CFBundleName": "XHS Leads",
        "CFBundleDisplayName": "XHS Leads",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
