# -*- mode: python ; coding: utf-8 -*-
"""XHSLeads macOS .app 打包配置（B 批壳）。

公共部分（datas / hiddenimports / Analysis·EXE·COLLECT 参数）在 spec_common.py，
本文件只保留 macOS 专有内容：BUNDLE 外壳、icns 图标、Info.plist。

要点：
- 入口 scripts/packaging/launcher.py（起 uvicorn + 开浏览器 + 优雅退出）。
- console=False：GUI 应用；launcher 已将 stdout/stderr 重定向到数据目录 logs/app.log。
- 内置引擎 CFT 不进 datas：其可执行二进制会被 PyInstaller 当 binary 处理失败
  （bincache 无法处理签名 app bundle），由 build_mac.sh 打包后 post-copy 到
  .app/Contents/Resources/engine/（R-6.11）。
"""
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821 —— SPECPATH 由 PyInstaller 注入

import spec_common as sc  # noqa: E402
from PyInstaller.utils.hooks import collect_data_files, collect_submodules  # noqa: E402

spec_dir, root = sc.paths(SPECPATH)  # noqa: F821

a = Analysis(
    datas=sc.datas(root) + collect_data_files("playwright"),
    hiddenimports=sc.hiddenimports(collect_submodules),
    excludes=sc.EXCLUDES_COMMON,
    **sc.analysis_kwargs(root, spec_dir / "launcher.py"),
)

pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], **sc.exe_kwargs())

coll = COLLECT(exe, a.binaries, a.datas, **sc.collect_kwargs())

app = BUNDLE(
    coll,
    name=f"{sc.APP_NAME}.app",
    icon=str(root / "build_resources" / "icon" / "XHSLeadsIcon.icns"),  # 品牌图标（build_mac.sh 生成）
    bundle_identifier="com.xhsleads.app",
    info_plist={
        # 品牌名统一为「XHS Leads · 小红书获客助手」（用户拍板：保留英文品牌 + 中文副标题）：
        # CFBundleName=短名（菜单栏 App 菜单），CFBundleDisplayName=完整名（Dock/Finder）。
        "CFBundleName": "XHS Leads",
        "CFBundleDisplayName": "XHS Leads 小红书获客助手",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
