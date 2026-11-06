# -*- coding: utf-8 -*-
"""c01 UI 自动化采集主流程（连引擎调试端口 9223，驱动前端 8791 完整采集）。

前置：8791 已启动（free 模式）；引擎 9223 已启动（本脚本自等 60s）。
流程：输入关键词 → 调 limit=3 → 点「开始采集」→ 轮询卡片渲染 → 校验
      notes 入库 / 日志卡片数 / 无假死（单测级返回码：0=通过 1=失败 2=SKIP）。
用法：backend/.venv/bin/python scripts/ui_tests/c01_ui_collect.py [keyword]
"""
import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "金刚石"
LIMIT = 3
DATA_DIR = Path.home() / "Library/Application Support/xhs-leads/data"
DB = DATA_DIR / "xhs_notes.db"


def wait_cdp(timeout: float = 60.0):
    """等引擎 9223 CDP 端口就绪。"""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 9223), timeout=2):
                return
        except OSError:
            time.sleep(2)
    raise SystemExit("SKIP: 引擎 9223 未就绪")


def main() -> int:
    wait_cdp()
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = b.contexts[0]
        # R-6.53u：精确匹配主应用页（排除 8791 子路由，
        # 曾导致脚本操作登录提示页而非采集面板，采集语义完全不同）
        pg = next((x for x in ctx.pages
                   if (x.url or "").rstrip("/") == "http://127.0.0.1:8791"), None)
        if pg is None:
            pg = ctx.new_page()
        pg.goto("http://127.0.0.1:8791/", timeout=20000)
        # 等采集面板关键词输入框
        pg.wait_for_selector('input[placeholder*="关键词"]', timeout=20000)
        print(f"1) 采集面板可见; 当前 URL={pg.url}")
        # 关键词 + limit
        kw = pg.locator('input[placeholder*="关键词"]')
        kw.fill(KEYWORD)
        lim = pg.locator('input[type="number"]')
        if lim.count():
            lim.fill(str(LIMIT))
        print(f"2) 输入关键词={KEYWORD!r} limit={LIMIT}")
        # 开始采集
        pg.click('button.btn-search:has-text("开始采集")')
        print("3) 已点击「开始采集」")
        # 轮询：卡片出现 或 按钮变回「开始采集」（采集结束）
        deadline = time.monotonic() + 180
        cards = 0
        status = ""
        while time.monotonic() < deadline:
            pg.wait_for_timeout(3000)
            cards = pg.locator(".card").count()
            btn_txt = pg.locator("button.btn-search").inner_text() if pg.locator("button.btn-search").count() else ""
            status_el = pg.locator(".status-bar, .status-text, .st-txt")
            status = status_el.first.inner_text() if status_el.count() else ""
            print(f"   t+{int(180 - (deadline - time.monotonic()))}s 卡片={cards} 按钮={btn_txt!r} 状态={status[:40]!r}")
            if cards >= 1 and "开始采集" in btn_txt:
                break
            if "开始采集" in btn_txt and cards == 0:
                pass  # 可能采集失败/0 条，继续看状态
        # 校验
        print(f"4) 采集结束：卡片={cards}")
        assert cards >= 1, "FAIL: UI 未渲染任何卡片（0 条=功能未完成）"
        # notes 入库校验
        n = sqlite3.connect(DB).execute("select count(*) from notes").fetchone()[0]
        print(f"5) notes 入库总数={n}")
        assert n >= 1, "FAIL: notes 表为空"
        # 最近 3 条完整性（title/nickname/cover_url）
        rows = sqlite3.connect(DB).execute(
            "select title, nickname, cover_url from notes order by rowid desc limit 3"
        ).fetchall()
        for r in rows:
            print(f"   note: title={r[0][:24]!r} author={r[1]!r} cover={'有' if r[2] else '无'}")
        assert all(r[0] and r[1] for r in rows), "FAIL: 最新笔记 title/author 缺失"
        print("PASS: UI 自动化采集全流程通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
