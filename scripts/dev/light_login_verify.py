#!/usr/bin/env python3
"""L2 轻量浏览器验证：登录页注入行为（不打包、不起完整 App）。

用途（验证分层 L2 层）：
  注入脚本 + 小红书登录页交互的秒级/分钟级验证，避免每次改动都走
  重打包 → 重启 → 连完整 App 的 L3 闭环（5-10 分钟）。

原理：
  - 直接 import backend/src/injections.py 的 JS 字符串（真实注入源）
  - 独立拉起 Chrome for Testing（独立 user-data-dir + CDP 9224，不碰 app 实例）
  - add_init_script（对应 app 的 init script 注册）+ evaluate 补注（对应 _ensure_login_injects）
  - 断言集对齐 48l 实机验证项

用法：
  bash scripts/dev/light_login_verify.sh          # 走封装（自启自停）
  python3 scripts/dev/light_login_verify.py       # 直接跑（需引擎已起或自起）

退出码：0 = 全部 PASS；1 = 有 FAIL。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

REPO = "/Users/wenjiechen/Desktop/codex-zhuke"
LOGIN_URL = "https://www.xiaohongshu.com/login?redirectPath=https%3A%2F%2Fwww.xiaohongshu.com%2F"
CDP_PORT = 9224
ENGINE = os.path.expanduser(
    "~/Library/Application Support/xhs-leads/engine/chrome-mac-x64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)

sys.path.insert(0, os.path.join(REPO, "backend"))
from src import injections  # noqa: E402


def wait_port(port: int, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            requests.get(f"http://127.0.0.1:{port}/json", timeout=1)
            return True
        except Exception:
            time.sleep(1)
    return False


def start_engine(profile: str):
    """拉起独立 CFT（复用项目内置引擎，独立配置目录+端口）。"""
    for p in (CDP_PORT,):
        out = subprocess.run(["lsof", "-ti", f":{p}"], capture_output=True, text=True)
        if out.stdout.strip():
            subprocess.run(["kill", "-9", *out.stdout.split()], capture_output=True)
            time.sleep(1)
    subprocess.Popen(
        [
            ENGINE,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            # 注意：必须非 headless——小红书对全新 profile 的 headless 请求返回
            # 安全限制 300012（当前网络存在风险）；有窗口全新 profile 实测放行。
            "--window-size=960,800",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_port(CDP_PORT):
        raise SystemExit(f"CFT 未能在 60s 内监听 9224（引擎路径: {ENGINE}）")


def main():
    fails = []

    def check(name: str, cond: bool, extra: str = ""):
        print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    profile = tempfile.mkdtemp(prefix="xhs-l2-")
    try:
        start_engine(profile)
        fl_js, fit_js = injections.get("force_login"), injections.get("login_fit")
        title_js = injections.get("shell_title")

        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            ctx = b.contexts[0] if b.contexts else b.new_context()
            page = ctx.new_page()
            # 模拟 app 注入方式：init script 注册（新 document 自动生效）
            page.add_init_script(fl_js)
            page.add_init_script(fit_js)
            page.add_init_script(title_js)

            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            time.sleep(9)
            # 补注当前 document（对应 _ensure_login_injects 的 evaluate 分支）
            page.evaluate(fl_js)
            page.evaluate(fit_js)
            page.evaluate(title_js)

            w = page.evaluate(
                "() => ({lock: window.__xhs_leads_lock_ok===true, "
                "fit: window.__xhs_leads_login_fit===true, "
                "mask: document.querySelectorAll('.reds-mask').length})"
            )
            check("注入标记+遮罩存在", w["lock"] and w["fit"] and w["mask"] > 0, str(w))

            full = page.evaluate(
                "() => { const c=document.querySelector('.container'); "
                "return c ? !!c.querySelector('canvas,[class*=qr],img[src*=qr]') : false; }"
            )
            check("登录组件完整(二维码)", bool(full))

            stable = True
            for pos in [(100, 100), (930, 120), (480, 780), (700, 100), (500, 30)]:
                page.mouse.move(*pos)
                page.mouse.down()
                time.sleep(0.12)
                page.mouse.up()
                time.sleep(0.8)
                st = page.evaluate(
                    "() => { const c=document.querySelector('.container'); "
                    "return c ? {vis:c.offsetWidth>0, txt:(c.textContent||'').trim().slice(0,18)} : 'gone'; }"
                )
                if st == "gone" or not st["vis"] or "活动" in st["txt"]:
                    stable = False
                    print(f"   点{pos} -> 异常: {st}")
                    break
            check("点空白组件不消失", stable)

            esc_ok = True
            for _ in range(2):
                page.keyboard.press("Escape")
                time.sleep(1)
                st = page.evaluate(
                    "() => { const c=document.querySelector('.container'); "
                    "return c ? {vis:c.offsetWidth>0, txt:(c.textContent||'').trim().slice(0,18)} : 'gone'; }"
                )
                if st == "gone" or not st["vis"] or "活动" in st["txt"]:
                    esc_ok = False
                    print(f"   ESC后 -> 异常: {st}")
                    break
            check("真实ESC不关", esc_ok)

            r = page.evaluate(
                "() => { const c=document.querySelector('.container'); "
                "const r=c.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width}; }"
            )
            page.mouse.click(int(r["x"] + r["w"] / 2), 300)
            time.sleep(1)
            check("组件内点击正常", page.evaluate(
                "() => { const c=document.querySelector('.container'); return c ? c.offsetWidth>0 : false; }"
            ))

            locked = page.evaluate(
                """() => { const els=document.querySelectorAll('[aria-label="关闭"],[aria-label="close"],.close-btn,.close-icon');
                let ok=true; for(const e of els){ if(getComputedStyle(e).display!=='none') ok=false; } return ok; }"""
            )
            check("关闭按钮锁定", bool(locked))
            check("标题守护", page.title() == "XHS Leads · 小红书获客助手", page.title()[:30])

            # 跨文档：新导航后 init script 自动生效（对应 48l 拉回后注入仍生效）
            page.goto("https://www.xiaohongshu.com/search_result/?keyword=abc", wait_until="domcontentloaded")
            time.sleep(4)
            w2 = page.evaluate(
                "() => ({lock: window.__xhs_leads_lock_ok===true, fit: window.__xhs_leads_login_fit===true})"
            )
            check("跨文档注入仍生效", w2["lock"] and w2["fit"])
            print("== " + ("ALL PASS" if not fails else f"FAILED: {fails}") + " ==")
            return 0 if not fails else 1
    finally:
        out = subprocess.run(["lsof", "-ti", f":{CDP_PORT}"], capture_output=True, text=True)
        if out.stdout.strip():
            subprocess.run(["kill", "-9", *out.stdout.split()], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
