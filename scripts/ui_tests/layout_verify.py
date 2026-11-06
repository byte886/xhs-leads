#!/usr/bin/env python3
"""R-6.22 双窗口分屏布局约定回归（R-6.31b 按 R-6.22p 关窗语义重写）。

约定（固定化，每次启动必须满足）：
- 左窗 = 主 APP 壳窗口（前端 127.0.0.1:8791/，XHS Leads·小红书获客助手）
  → **应用模式**（window.open 新页落新窗口而非本窗口内新 tab）+
    bounds.left == 0, width == min(1280, 主屏宽//2), height == 800
- 右窗 = Tab2（基线感知，两种形态都算「右窗」）：
  · 未登录态 = https://www.xiaohongshu.com/login（右窗直接是扫码页）
  · 已登录态 = xiaohongshu.com 采集调试窗（非 /login）
  → bounds.left == 窗口宽（与左窗等宽并排）, width == 窗口宽, height == 800
- 不重叠：shell.left + shell.width <= tab2.left（严格相邻，允许 0 间隙）

R-6.22p 关窗语义（section 2，旧「Tab1 四连关重建」已与现行语义矛盾，删除）：
- (a) 关 Tab2（采集窗）→ 断言：Tab2 **不复活**、应用**不退出**（login_status 仍 200、
  壳窗仍在）；
- (b) 关主窗 = 整体退出应用：**不在本脚本默认流程**（--smoke-shell-close 显式开启才跑，
  不进 run_all）。原因：该动作会终止被测 dev 实例，run_all 串行流程中会误杀后端；
  且 R-6.22p 已在代码/日志层覆盖（on_shell_closed → launcher 退出信号）。

安全约束：本脚本只读布局 + 仅主动关闭 Tab2 测试窗；不杀进程、不重启实例；
结束时恢复「壳窗 + 小红书登录页」2 页基线（开关 false→true 由服务端重建采集窗）。

用法：backend/.venv/bin/python scripts/ui_tests/layout_verify.py [--smoke-shell-close] [--screenshot /tmp/layout.png]
依赖：dev 实例在跑（API 8791 + CDP 9223），串行运行（勿与 t 系列并发）。
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8791"
CDP = "http://127.0.0.1:9223"
BASE = "http://127.0.0.1:8791"


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


def _norm(url: str) -> str:
    """8791 URL → path（去 origin/hash/尾斜杠）；非 8791 原样返回。"""
    if "127.0.0.1:8791" not in (url or ""):
        return url or ""
    rest = (url or "").split("127.0.0.1:8791", 1)[1].split("#", 1)[0]
    if rest in ("", "/"):
        return "/"
    return rest.rstrip("/") or "/"


def _is_shell(pg):
    """壳窗 = 8791 根路径页（'/'）；采集窗在 xiaohongshu.com，不会误判。"""
    return _norm(pg.url) == "/"


def _is_tab2(pg):
    """采集窗 = 任何小红书页（R-6.53z8k：未登录落 /login，已登录落浏览页）。"""
    return "xiaohongshu.com" in (pg.url or "")


def _expected_geo() -> tuple:
    """期望几何：与 engine_pool.shell_window_geometry 同算法——
    win_w = min(1280, 主屏宽//2)，高 800。失败回退 1920×800（=960 宽）。"""
    w = 1920
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            if "Resolution:" in line and " x " in line:
                nums = re.findall(r"(\d+)\s*[xX]\s*(\d+)", line)
                if nums:
                    w = int(nums[0][0])
                    break
    except Exception:  # noqa: BLE001（探测失败回退 1920）
        pass
    return min(1280, w // 2), 800


def _login_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{API}/api/login_status", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


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
    ap.add_argument("--smoke-shell-close", action="store_true",
                    help="[独立 smoke，不进 run_all] 关壳窗→断言应用退出；跑完需手动重启 launcher。")
    ap.add_argument("--screenshot", default="", help="可选：验证后截屏路径")
    args = ap.parse_args()

    exp_w, exp_h = _expected_geo()
    print(f"期望几何: width={exp_w} height={exp_h}（min(1280, 主屏宽//2)）")

    # 1) 两窗就绪：左壳窗 + 右 Tab2（基线感知）
    _json_post(f"{API}/api/app_config", {"collect_tab_visible": True})

    with sync_playwright() as p:
        shell = tab2 = None
        for _ in range(30):
            b = _connect(p)
            shell = _find(b, _is_shell)
            tab2 = _find(b, _is_tab2)
            if shell and tab2:
                break
            b.close()
            time.sleep(0.5)
        if not shell or not tab2:
            print(f"FAIL: 两窗口未齐（壳={bool(shell)} Tab2={bool(tab2)}）")
            return 1

        sb, cb = _bounds_of(shell), _bounds_of(tab2)
        checks = [
            ("左窗=主APP（8791 根路径）", _is_shell(shell)),
            ("右窗=采集窗（xiaohongshu 页）", _is_tab2(tab2)),
            ("左窗应用模式（window.open 开新窗口）", _is_app_window(b, shell)),
            ("左窗在右窗左侧（shell.left < tab2.left）", sb["left"] < cb["left"]),
            ("左窗贴主屏左缘（left==0）", sb["left"] == 0),
            (f"左窗宽=={exp_w}（动态半屏宽）", sb["width"] == exp_w),
            (f"右窗宽=={exp_w}（与左窗等宽）", cb["width"] == exp_w),
            ("两窗同高", sb["height"] == cb["height"]),
            (f"高度=={exp_h}（G7 断言）", sb["height"] == exp_h and cb["height"] == exp_h),
            (f"右窗 left==左窗 left+width（{sb['left']}+{sb['width']}）",
             cb["left"] == sb["left"] + sb["width"]),
            ("不重叠（left+width<=右窗left）", sb["left"] + sb["width"] <= cb["left"]),
        ]
        print("== section1 布局约定 ==")
        print(f"  左(主APP) : {sb}")
        print(f"  右(Tab2)  : {cb}  url={tab2.url[:50]}")
        for name, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not all(ok for _, ok in checks):
            return 1

        # 2a) R-6.22p：关 Tab2 → 不复活、应用不退（等 ~10s = 2 个 keeper 5s 轮询）
        print("== section2a 关 Tab2（R-6.22p：不复活/应用不退） ==")
        tid = [t for t in _tabs() if "xiaohongshu.com" in t["url"]]
        if not tid:
            print("FAIL: 未找到 Tab2 目标（无法执行关窗语义断言）")
            return 1
        _close(tid[0]["id"])
        print(f"  已关闭 Tab2: {tid[0]['url'][:60]}")
        time.sleep(10.0)

        alive = _login_alive()
        b2 = _connect(p)
        shells_left = [pg for pg in b2.contexts[0].pages if _is_shell(pg)]
        tab2_resurrect = [pg for pg in b2.contexts[0].pages if _is_tab2(pg)]
        b2.close()
        close_checks = [
            (f"应用未退出（login_status=200，实测={alive}）", alive),
            (f"壳窗仍在（恰 1 个，实测={len(shells_left)}）", len(shells_left) == 1),
            (f"Tab2 不复活（实测残留={len(tab2_resurrect)}）", len(tab2_resurrect) == 0),
        ]
        for name, ok in close_checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not all(ok for _, ok in close_checks):
            return 1

        # 2b) 可选独立 smoke：关主窗=退出应用（默认不跑，保护 run_all 流程）
        if args.smoke_shell_close:
            print("== section2b [opt-in] 关主窗 → 应用退出 ==")
            stid = [t for t in _tabs() if _norm(t["url"]) == "/"]
            if not stid:
                print("FAIL: 未找到壳窗目标")
                return 1
            _close(stid[0]["id"])
            time.sleep(6.0)
            exited = not _login_alive()
            print(f"  [{'PASS' if exited else 'FAIL'}] 关壳窗后应用退出（login_status 不通，实测={not exited}）")
            if not exited:
                return 1
            print("  注意：dev 实例已退出，请按回归命令重启 launcher 恢复基线。")
            b.close()
            return 0

        # 3) 恢复基线：开关 false→true 由服务端重建采集窗，keeper 导航到小红书登录页
        print("== section3 恢复未登录 2 页基线 ==")
        _json_post(f"{API}/api/app_config", {"collect_tab_visible": False})
        time.sleep(1.5)
        _json_post(f"{API}/api/app_config", {"collect_tab_visible": True})
        # 等 keeper 把新建采集窗导航到 xiaohongshu /login
        ok = False
        for _ in range(12):
            time.sleep(1.5)
            tabs = _tabs()
            urls = sorted(_norm(t["url"]) for t in tabs)
            if (len(tabs) == 2 and any(_norm(t["url"]) == "/" for t in tabs)
                    and any("xiaohongshu.com" in t["url"] for t in tabs)):
                ok = True
                break
        urls = sorted(_norm(t["url"]) for t in _tabs())
        print(f"  恢复后页面集 path={urls}")
        if not ok:
            print(f"FAIL: 基线未恢复（期望 [8791 /, xiaohongshu]，实测 {urls}）")
            return 1
        sb2 = _bounds_of(_find(_connect(p), _is_shell))
        print(f"  壳窗 bounds={sb2}（应保持 left=0 width={exp_w} height={exp_h}）")
        b.close()

    if args.screenshot:
        subprocess.run(["screencapture", "-x", args.screenshot], check=False)

    print("\nSUMMARY: 双窗口布局 + R-6.22p 关窗语义 ALL PASS（左主APP/右Tab2/等宽并排/"
          "Tab2关闭不复活/基线已恢复）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
