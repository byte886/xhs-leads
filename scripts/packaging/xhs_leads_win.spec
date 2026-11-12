# -*- mode: python ; coding: utf-8 -*-
"""XHSLeads Windows（win64 / onedir）打包配置。

公共部分（datas / hiddenimports / Analysis·EXE·COLLECT 参数）在 spec_common.py，
本文件只保留 Windows 专有内容：EXE 的 ico 图标、macOS 桥接相关 excludes。

与 macOS 的布局差异：
- 无 BUNDLE 概念，EXE + COLLECT 即 onedir；datas 落在 _internal/（或 dist/XHSLeads/），
  engine_types 的 builtin_cft_candidates() 已按平台分支解析 chrome-win64/chrome.exe。
- 内置引擎 CFT（chrome-win64）同样不进 datas：build_win.ps1 post-copy 到
  dist/XHSLeads/_internal/engine/。
- console=False：窗口化应用，日志由 launcher 落盘到
  %LOCALAPPDATA%\\xhs-leads\\data\\logs\\app.log。

构建（必须在 Windows 上，PyInstaller 不能跨平台交叉编译）：
    python -m PyInstaller --noconfirm --clean scripts/packaging/xhs_leads_win.spec
"""
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821 —— SPECPATH 由 PyInstaller 注入

import spec_common as sc  # noqa: E402
from PyInstaller.utils.hooks import collect_data_files, collect_submodules  # noqa: E402

spec_dir, root = sc.paths(SPECPATH)  # noqa: F821

a = Analysis(
    datas=sc.datas(root) + collect_data_files("playwright"),
    hiddenimports=sc.hiddenimports(
        collect_submodules, extra=sc.WINDOWS_EXTRA_HIDDENIMPORTS
    ),
    excludes=sc.EXCLUDES_WINDOWS,
    **sc.analysis_kwargs(root, spec_dir / "launcher.py"),
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    **sc.exe_kwargs(root / "build_resources" / "icon" / "XHSLeadsIcon.ico"),
)

coll = COLLECT(exe, a.binaries, a.datas, **sc.collect_kwargs())
