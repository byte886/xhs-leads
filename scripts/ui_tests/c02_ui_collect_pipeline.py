# -*- coding: utf-8 -*-
"""c02 UI 自动化：流水线语义双场景（R-6.53z / z2 起生效）。

前提：8791 已启动（free 模式，跑的是当前代码）；引擎 9223 已启动。
两个场景都校验「UI 卡片数 == DB 条数」，即"采集多少条、进列表多少条"恒等——
collect_start 会先清空旧库（R-6.25 用户拍板），所以 DB 总数就是本次采集结果。

  stop：采集中看到 >=2 卡片即点停止 → 停止时卡片数、停止后卡片数、DB 条数三者相等
        （对应"停止=所见即所得，停止后不得继续增长"）
  auto：不点停止，采满 limit 自动收尾 → 卡片数、limit、DB 条数三者相等

用法：backend/.venv/bin/python scripts/ui_tests/c02_ui_collect_pipeline.py [stop|auto] [keyword]
返回码：0=通过 1=失败 2=SKIP（8791/9223 未就绪）
"""
import socket
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SCENARIO = (sys.argv[1] if len(sys.argv) > 1 else "stop").lower()
KEYWORD = sys.argv[2] if len(sys.argv) > 2 else ("翡翠手串" if SCENARIO == "stop" else "翡翠平安扣")
LIMIT = 3
APP_URL = "http://127.0.0.1:8791"
DB = Path.home() / "Library/Application Support/xhs-leads/data/xhs_notes.db"
STOP_THRESHOLD = 2  # stop 场景：看到几张卡片时按停止


def port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def db_count() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        return int(con.execute("select count(*) from notes").fetchone()[0])
    finally:
        con.close()


def main() -> int:
    if not port_open(8791) or not port_open(9223):
        print("SKIP: 8791 或 9223 未就绪", flush=True)
        return 2
    failures = []
    stopped_at = None
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        for p in ctx.pages:
            if (p.url or "").rstrip("/") == APP_URL:
                page = p
                break
        if page is None:
            page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        page.goto(APP_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        kw = page.locator('input[placeholder*="关键词"]')
        if kw.count() == 0:
            print("FAIL: 未找到关键词输入框", flush=True)
            browser.close()
            return 1
        kw.fill(KEYWORD)
        num = page.locator('input[type="number"]')
        if num.count() > 0:
            num.first.fill(str(LIMIT))

        btn = page.locator("button.btn-search").first
        print(f"场景={SCENARIO} 关键词={KEYWORD} limit={LIMIT} 按钮初始={(btn.inner_text()).strip()!r}", flush=True)
        btn.click()
        page.wait_for_timeout(2000)

        last = None
        for i in range(150):
            bt = btn.inner_text().strip()
            cards = page.locator(".card").count()
            try:
                st = page.locator(".status-bar,.status-text,.st-txt").first.inner_text().strip()
            except Exception:
                st = ""
            if (bt, cards, st) != last:
                print(f"  [{i * 2}s] btn={bt!r} cards={cards} status={st!r}", flush=True)
                last = (bt, cards, st)

            if SCENARIO == "stop":
                if "停止" in bt and cards >= STOP_THRESHOLD and stopped_at is None:
                    stopped_at = cards
                    print(f"  → 点击停止（此刻卡片={cards}）", flush=True)
                    btn.click()
                if "开始" in bt and stopped_at is not None:
                    final = page.locator(".card").count()
                    print(f"  停止收尾：停止时={stopped_at} 停止后={final} DB={db_count()}", flush=True)
                    if final != stopped_at:
                        failures.append(f"停止后卡片 {final} != 停止时 {stopped_at}（停止未完全生效）")
                    if db_count() != stopped_at:
                        failures.append(f"DB {db_count()} != 停止时 {stopped_at}（列表与库不一致）")
                    break
            else:  # auto
                if "开始" in bt and i > 1:
                    final = page.locator(".card").count()
                    print(f"  自动完成：卡片={final} limit={LIMIT} DB={db_count()}", flush=True)
                    if final != LIMIT:
                        failures.append(f"自动完成后卡片 {final} != limit {LIMIT}")
                    if db_count() != final:
                        failures.append(f"DB {db_count()} != 卡片 {final}（列表与库不一致）")
                    break
            page.wait_for_timeout(2000)
        else:
            failures.append("300s 未收敛（停止/完成未收尾）")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", flush=True)
        return 1
    print(f"PASS: {SCENARIO} 场景 UI 卡片数 = DB 条数 = {db_count()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
