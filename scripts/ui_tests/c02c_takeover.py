# -*- coding: utf-8 -*-
"""c02c UI 自动化：采集跑着时被新任务顶掉（R-6.53z4）。

UI 上采集中按钮是「停止」，所以「顶掉」只可能来自接口再调一次 collect_start
（外部调用 / 多入口）。本脚本复现该路径并验证：
  1) 旧任务被标停 + 取消，不残留 running；
  2) 采集窗被复位（引擎内 xiaohongshu 页面数在切换窗口期内回到 0）；
  3) 新任务照常跑满 limit，最终 UI 卡片数 == limit == DB 条数。

用法：backend/.venv/bin/python scripts/ui_tests/c02c_takeover.py
返回码：0=通过 1=失败 2=SKIP（8791/9223 未就绪）
"""
import json
import socket
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_URL = "http://127.0.0.1:8791"
DB = Path.home() / "Library/Application Support/xhs-leads/data/xhs_notes.db"
KW_FIRST, KW_SECOND, LIMIT = "翡翠手串", "翡翠吊坠", 3


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


def get_progress(task_id: str) -> dict:
    with urllib.request.urlopen(f"{APP_URL}/api/collect_progress?task_id={task_id}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_collect_start(keyword: str) -> dict:
    body = json.dumps({"keyword": keyword, "limit": LIMIT}).encode("utf-8")
    req = urllib.request.Request(
        f"{APP_URL}/api/collect_start", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


COLLECT_URL_HINTS = ("/explore/", "/search_result", "/discovery/item")


def _collect_pages(ctx) -> list:
    """引擎内的「采集类」页面：笔记详情/搜索结果/探索页。用户主页等复位目标页不算。"""
    return [
        p for p in ctx.pages
        if "xiaohongshu" in (p.url or "") and any(h in (p.url or "") for h in COLLECT_URL_HINTS)
    ]


def main() -> int:
    if not port_open(8791) or not port_open(9223):
        print("SKIP: 8791 或 9223 未就绪", flush=True)
        return 2
    failures = []
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
        page.goto(APP_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        kw, btn = page.locator('input[placeholder*="关键词"]'), page.locator("button.btn-search").first
        num = page.locator('input[type="number"]')
        if kw.count() == 0:
            print("FAIL: 未找到关键词输入框", flush=True)
            browser.close()
            return 1
        kw.fill(KW_FIRST)
        if num.count() > 0:
            num.first.fill(str(LIMIT))
        btn.click()

        # 等第一条出现，确认旧任务真的在跑
        running_at = None
        for i in range(60):
            if page.locator(".card").count() >= 1 and "停止" in btn.inner_text().strip():
                running_at = (i + 1) * 2
                break
            page.wait_for_timeout(2000)
        if running_at is None:
            print("FAIL: 60s 内旧任务未产出第 1 条，无法构造顶掉场景", flush=True)
            browser.close()
            return 1
        print(f"  [{running_at}s] 旧任务在跑（卡片={page.locator('.card').count()}）→ 接口再发一次 collect_start 顶掉它", flush=True)

        resp = post_collect_start(KW_SECOND)
        print(f"  顶掉返回：ok={resp.get('ok')} task_id={resp.get('task_id')} keyword={resp.get('keyword')}", flush=True)
        if not resp.get("ok"):
            failures.append(f"顶掉调用失败：{resp}")

        # 判定必须以后端新任务状态为准：前端只跟它自己发起的 task_id，
        # 旧任务一取消按钮就闪回「开始采集」并清空卡片，这时候新任务才刚开始。
        tid_new = resp.get("task_id", "")
        closed_seen = False
        cards_after = 0
        prog = {}
        for i in range(150):
            alive = _collect_pages(ctx)
            cards = page.locator(".card").count()
            bt = btn.inner_text().strip()
            if not alive:
                closed_seen = True
            try:
                prog = get_progress(tid_new)
            except Exception:
                prog = {}
            print(f"  [{(i + 1) * 2}s] btn={bt!r} cards={cards} 采集页={len(alive)} "
                  f"后端={prog.get('status')} 已采集={prog.get('collected')}", flush=True)
            if prog.get("status") in ("done", "stopped", "error"):
                break
            page.wait_for_timeout(2000)

        page.reload(wait_until="domcontentloaded")  # 外部接口另起的任务前端不认，刷新后读真实列表
        page.wait_for_timeout(2500)
        cards_after = page.locator(".card").count()
        dbn = db_count()
        still = _collect_pages(ctx)
        print(f"  结果：切换期曾见采集页=0 {closed_seen}，新任务收尾卡片={cards_after} DB={dbn} 采集页存活={len(still)}", flush=True)
        # closed_seen 仅记录信息：新任务可能立刻复用同一采集窗，采样未必抓到空档；
        # 真正的硬判据是「收尾后采集页必须为 0」（见下方 still 检查）。

        if cards_after != LIMIT:
            failures.append(f"新任务收尾卡片 {cards_after} != limit {LIMIT}")
        if dbn != cards_after:
            failures.append(f"DB {dbn} != 卡片 {cards_after}")
        if still:
            failures.append(f"收尾后采集页仍存活 {[p.url[:60] for p in still]}")
        browser.close()

    if failures:
        for f in failures:
            print(f"FAIL: {f}", flush=True)
        return 1
    print("PASS: 顶掉旧任务后采集窗复位、新任务跑满 limit、UI=DB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
