# -*- coding: utf-8 -*-
"""c02b UI 自动化：停止后必须「完全停止」（R-6.53 口径）。

c02 只验证「停止那一刻卡片=DB」。本脚本验证停止之后：
  1) 采集页 20s 内没有任何小红书网络请求（CDP Network 计数）——采集窗真的停了；
  2) 20s 内 UI 卡片数与 DB 条数逐 5s 采样都不再变化——没有后台继续入库。

用法：backend/.venv/bin/python scripts/ui_tests/c02b_stop_settle.py [keyword]
返回码：0=通过 1=失败 2=SKIP（8791/9223 未就绪）
"""
import socket
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "翡翠手串"
LIMIT = 3
STOP_THRESHOLD = 2
SETTLE_SECONDS = 20
APP_URL = "http://127.0.0.1:8791"
DB = Path.home() / "Library/Application Support/xhs-leads/data/xhs_notes.db"


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def db_count() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        return int(con.execute("select count(*) from notes").fetchone()[0])
    finally:
        con.close()


def collect_pages(ctx):
    """引擎里所有小红书采集页（停止后应为空 = 采集窗已复位）。"""
    return [p for p in ctx.pages if "xiaohongshu" in (p.url or "")]


def find_page(ctx, needle: str):
    """精确匹配 APP 页（needle=APP_URL 时排除 /login-hint 子页）。"""
    for p in ctx.pages:
        url = (p.url or "").rstrip("/")
        if needle == APP_URL:
            if url == APP_URL:
                return p
        elif needle in url:
            return p
    return None


def main() -> int:
    if not port_open(8791) or not port_open(9223):
        print("SKIP: 8791 或 9223 未就绪", flush=True)
        return 2
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = find_page(ctx, APP_URL)
        if page is None:
            page = ctx.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        kw = page.locator('input[placeholder*="关键词"]')
        num = page.locator('input[type="number"]')
        btn = page.locator("button.btn-search").first
        if kw.count() == 0:
            print("FAIL: 未找到关键词输入框", flush=True)
            browser.close()
            return 1
        kw.fill(KEYWORD)
        if num.count() > 0:
            num.first.fill(str(LIMIT))
        btn.click()
        page.wait_for_timeout(1500)

        stopped_at = None
        for i in range(120):
            bt = btn.inner_text().strip()
            cards = page.locator(".card").count()
            if "停止" in bt and cards >= STOP_THRESHOLD and stopped_at is None:
                stopped_at = cards
                print(f"  [{(i + 1) * 2}s] 点击停止（卡片={cards}）", flush=True)
                btn.click()
                break
            if i % 5 == 0:
                print(f"  [{(i + 1) * 2}s] btn={bt!r} cards={cards}", flush=True)
            page.wait_for_timeout(2000)
        if stopped_at is None:
            print("FAIL: 未能在采集态按到停止", flush=True)
            browser.close()
            return 1

        # 等按钮回空闲
        for _ in range(30):
            if "开始" in btn.inner_text().strip():
                break
            page.wait_for_timeout(1000)
        if "开始" not in btn.inner_text().strip():
            failures.append("停止后按钮未回到「开始采集」")

        # 判据 A：采集页已从引擎消失（窗口复位）；判据 B：若仍在，停止后请求数必须为 0
        alive = collect_pages(ctx)
        req_after = {"n": 0, "urls": []}
        if not alive:
            print("  采集页已关闭（引擎内无 xiaohongshu 页面）= 完全停止", flush=True)
        else:
            print(f"  采集页仍存活 {len(alive)} 个，改用请求计数判据：{alive[0].url[:80]}", flush=True)
            cdp = ctx.new_cdp_session(alive[0])
            cdp.send("Network.enable")
            def _on_req(evt):
                url = (evt.get("request") or {}).get("url", "")
                if "xiaohongshu" in url:
                    req_after["n"] += 1
                    if len(req_after["urls"]) < 3:
                        req_after["urls"].append(url[:90])
            cdp.on("Network.requestWillBeSent", _on_req)

        base_cards, base_db = page.locator(".card").count(), db_count()
        t0 = time.time()
        while time.time() - t0 < SETTLE_SECONDS:
            page.wait_for_timeout(5000)
            cards, dbn = page.locator(".card").count(), db_count()
            print(f"  [{int(time.time() - t0)}s] settle cards={cards} db={dbn} net_req={req_after['n']}", flush=True)
            if cards != base_cards:
                failures.append(f"停止后卡片从 {base_cards} 涨到 {cards}")
                break
            if dbn != base_db:
                failures.append(f"停止后 DB 从 {base_db} 涨到 {dbn}")
                break

        still = collect_pages(ctx)
        print(f"  结论：停止时卡片={stopped_at} 静置后卡片={page.locator('.card').count()} DB={db_count()} "
              f"采集页存活={len(still)} 停止后小红书请求={req_after['n']}", flush=True)
        if still:
            failures.append(f"停止后采集页仍存活 {len(still)} 个（采集窗未复位）：{[p.url[:60] for p in still]}")
        if req_after["n"] > 0:
            failures.append(f"停止后仍有 {req_after['n']} 个小红书请求（CDP 未完全停止）：{req_after['urls']}")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", flush=True)
        return 1
    print("PASS: 停止后 20s 无新请求、卡片与 DB 均不变", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
