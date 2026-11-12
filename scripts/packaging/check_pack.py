#!/usr/bin/env python3
"""打包产物自检：两端共用，缺一个随包资产就判定打包失败。

为什么需要它：PyInstaller 对 datas 里"目录存在但文件不全"不会报错，spec 少写一项
就变成"打包成功、功能静默缺失"——2026-11 就发生过只拷 extension/lib、漏掉
manifest.json + background.js，导致打包版加载不了扩展、没有「采集窗口」标签组，
而启动日志只有一条 info。这个脚本把"产物里必须有什么"写成可执行清单，
两个平台构建脚本都调用它。

用法：
    python scripts/packaging/check_pack.py dist/XHSLeads.app      # macOS
    python scripts/packaging/check_pack.py <dist>/XHSLeads         # Windows onedir
    python scripts/packaging/check_pack.py <dir> --no-engine       # 跳过引擎校验

退出码：0 全部就位；1 有缺失（缺什么打印什么）。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

# 随包资产清单：(相对资源根的路径, 说明)。两端一致。
COMMON_ASSETS: list[tuple[str, str]] = [
    ("extension/manifest.json", "扩展声明（缺失会让扩展加载被静默跳过）"),
    ("extension/background.js", "扩展本体（采集窗标签组/认领）"),
    ("extension/lib/extract.js", "DOM 提取脚本"),
    ("frontend/dist/index.html", "前端构建产物"),
]

ENGINE_ASSETS = {
    "darwin": (
        "engine/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/"
        "Google Chrome for Testing",
        "内置引擎 CFT（macOS）",
    ),
    "win32": ("engine/chrome-win64/chrome.exe", "内置引擎 CFT（Windows）"),
}


def resource_root(dist: pathlib.Path) -> pathlib.Path:
    """把产物路径归一成"随包资源根"。

    - macOS：<X>.app → Contents/Resources
    - Windows onedir：<root>/_internal（PyInstaller 6+），旧布局直接用 <root>
    - 已经给了资源根本身则原样返回
    """
    if (dist / "Contents" / "Resources").is_dir():
        return dist / "Contents" / "Resources"
    if (dist / "_internal").is_dir():
        return dist / "_internal"
    return dist


def main() -> int:
    ap = argparse.ArgumentParser(description="打包产物资产自检")
    ap.add_argument("dist", help="产物路径（.app 目录 / onedir 目录 / 资源根）")
    ap.add_argument("--no-engine", action="store_true", help="跳过内置引擎校验")
    ap.add_argument("--platform", default=sys.platform, help="darwin / win32（默认当前平台）")
    args = ap.parse_args()

    dist = pathlib.Path(args.dist).resolve()
    if not dist.exists():
        print(f"FAIL 产物路径不存在: {dist}")
        return 1
    res = resource_root(dist)

    checks: list[tuple[pathlib.Path, str]] = [
        (res / rel, desc) for rel, desc in COMMON_ASSETS
    ]
    if not args.no_engine:
        eng = ENGINE_ASSETS.get(args.platform)
        if eng:
            checks.append((res / eng[0], eng[1]))
        if args.platform == "win32":
            checks.append((dist / "XHSLeads.exe", "主程序"))

    print(f"产物根: {dist}")
    print(f"资源根: {res}")
    missing: list[tuple[pathlib.Path, str]] = []
    for path, desc in checks:
        if path.is_file():
            print(f"OK   {path.relative_to(res) if res in path.parents else path.name}  — {desc}")
        else:
            missing.append((path, desc))
            print(f"MISS {path}  — {desc}")

    if missing:
        print(f"\nFAIL 缺 {len(missing)} 项，打包产物不可用")
        return 1
    print(f"\nPASS {len(checks)} 项随包资产齐全")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
