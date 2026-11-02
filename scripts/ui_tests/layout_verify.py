#!/usr/bin/env python3
"""R-6.22 双窗口分屏布局约定回归（左主 APP / 右采集调试窗口 / 不重叠）。

约定（固定化，每次启动必须满足）：
- 左窗 = 主 APP 壳窗口（前端 127.0.0.1:8791，XHS Leads·小红书获客助手）
  → **应用模式**（无标签栏/Tab 页面：window.open 新页落新窗口而非本窗口内
    新 tab）+ bounds.left == 0, width == min(1280, 主屏宽//2), top 固定
- 右窗 = 采集调试窗口（小红书 explore/任务页，xiaohongshu.com 非 /login）
  → bounds.left == 窗口宽（与左窗等宽并排）, width == 窗口宽
- 不重叠：shell.left + shell.width <= collect.left（严格相邻，允许 0 间隙）
- 窗口被关后 keeper 重建，布局必须保持（四连关 Tab1 回归）

用法：backend/.venv/bin/python scripts/ui_tests/layout_verify.py [--screenshot /tmp/layout.png]
依赖：dev 实例在跑（API 8791 + CDP 9223），串行运行（勿与 t 系列并发）。
"""
import argparse
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8791"
CDP = "http://127.0.0.1:9223"


def _json_get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def _json_post(url: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _tabs():
    return [t for t in _json_get(f"{CDP}/json/list") if t["type"] == "page"]


def _close(tid: str):
    urllib.request.urlopen(f"{CDP}/json/close/{tid}", timeout=8).read()


def _connect(p):
    return p.chromium.connect_over_cdp(CDP)


def _find(b, pred):
    for ctx in b.contexts:
        for pg in ctx.pages:
            if pred(pg):
                return pg
    return None


def _bounds_of(pg):
    s = pg.context.new_cdp_session(pg)
    t = s.send("Browser.getWindowForTarget")
    return s.send("Browser.getWindowBounds", {"windowId": t["windowId"]})["bounds"]


def _is_shell(pg):
    return "127.0.0.1:8791" in (pg.url or "")


def _is_collect(pg):
    u = pg.url or ""
    return "xiaohongshu.com" in u and "/login" not in u


def _window_id(pg):
    s = pg.context.new_cdp_session(pg)
    return s.send("Browser.getWindowForTarget")["windowId"]


def _is_app_window(b, pg):
    """R-6.22c 应用模式判定：window.open 的新页落在新窗口（windowId 与本窗口
    不同），而非本窗口内新 tab——即壳窗口无标签栏/Tab 页面（用户约定硬性要求）。"""
    with b.contexts[0].expect_page(timeout=5000) as pinfo:
        pg.evaluate("() => window.open('about:blank')")
    newp = pinfo.value
    try:
        return _window_id(newp) != _window_id(pg)
    finally:
        newp.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screenshot", default="", help="可选：验证后截屏路径")
    args = ap.parse_args()

    # 1) 开关打开：确认左主 APP + 右采集窗口
    _json_post(f"{API}/api/app_config", {"collect_tab_visible": True})

    with sync_playwright() as p:
        # 等待两窗就绪
        shell = collect = None
        for _ in range(30):
            b = _connect(p)
            shell = _find(b, _is_shell)
            collect = _find(b, _is_collect)
            if shell and collect:
                break
            b.close()
            time.sleep(0.5)
        if not shell or not collect:
            print("FAIL: 两窗口未齐（壳或采集窗口缺失）")
            return 1

        sb, cb = _bounds_of(shell), _bounds_of(collect)

        # 左主 APP / 右调试的语义断言
        checks = [
            ("左窗=主APP（URL 127.0.0.1:8791）", _is_shell(shell)),
            ("右窗=采集调试（URL xiaohongshu）", _is_collect(collect)),
            ("左窗应用模式（无Tab页：window.open开新窗口）", _is_app_window(b, shell)),
            ("左窗在右窗左侧（shell.left < collect.left）", sb["left"] < cb["left"]),
            ("左窗贴主屏左缘（left==0）", sb["left"] == 0),
            ("两窗等宽（%s==%s）" % (sb["width"], cb["width"]), sb["width"] == cb["width"]),
            ("两窗同高（%s==%s）" % (sb["height"], cb["height"]), sb["height"] == cb["height"]),
            ("不重叠（left+width<=右窗left）",
             sb["left"] + sb["width"] <= cb["left"]),
            ("窗口宽为半屏宽（%s，主屏1920//2）" % sb["width"], sb["width"] == 960),
        ]
        print("== 开开关后 ==")
        print(f"  左(主APP)   : {sb}")
        print(f"  右(调试)    : {cb}")
        for name, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not all(ok for _, ok in checks):
            return 1

        # 2) keeper 重建回归：Tab1 四连关，每轮布局保持
        for i in range(4):
            _close([t for t in _tabs() if "127.0.0.1:8791" in t["url"]][0]["id"])
            time.sleep(2.5)
            b = _connect(p)
            shell = _find(b, _is_shell)
            collect = _find(b, _is_collect)
            if not shell or not collect:
                print(f"FAIL: round{i} 重建后窗口缺失")
                return 1
            sb, cb = _bounds_of(shell), _bounds_of(collect)
            app_ok = _is_app_window(b, shell)
            ok = (
                app_ok
                and sb["width"] == 960 and cb["width"] == 960
                and sb["left"] == 0 and cb["left"] == 960
                and sb["left"] + sb["width"] <= cb["left"]
            )
            print(f"  round{i}: shell={sb} collect={cb} app模式={app_ok} overlap={not ok} -> {'PASS' if ok else 'FAIL'}")
            if not ok:
                return 1
            b.close()

        # 3) 收尾：恢复开关关（采集窗口收起，仅留主 APP 左窗）
        _json_post(f"{API}/api/app_config", {"collect_tab_visible": False})
        time.sleep(1.5)
        b = _connect(p)
        shells = [pg for pg in b.contexts[0].pages if _is_shell(pg)]
        collects = [pg for pg in b.contexts[0].pages if _is_collect(pg)]
        if len(shells) != 1 or collects:
            print(f"FAIL: 收尾异常 shell={len(shells)} collect={len(collects)}")
            return 1
        sb = _bounds_of(shells[0])
        app_ok = _is_app_window(b, shells[0])
        if sb["width"] != 960 or sb["left"] != 0 or not app_ok:
            print(f"FAIL: 收尾主APP布局 {sb} app模式={app_ok}")
            return 1
        print("== 开关关后 ==")
        print(f"  仅左主APP: {sb} app模式={app_ok} -> PASS")
        b.close()

    if args.screenshot:
        import subprocess
        subprocess.run(["screencapture", "-x", args.screenshot], check=False)

    print("\nSUMMARY: 双窗口分屏布局约定回归 ALL PASS（左主APP/右调试/等宽并排/重建保持）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
