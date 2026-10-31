# -*- coding: utf-8 -*-
from pathlib import Path
import sqlite3
if not sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute('select count(*) from clients').fetchone()[0]:
    print('SKIP: 无客户数据（clients 为空，需先「采集客户」）')
    raise SystemExit(2)
from playwright.sync_api import sync_playwright

lines = []
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = ctx.new_page()
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:8791/?stage=pick", wait_until="domcontentloaded")
    pg.wait_for_timeout(1800)
    pg.locator('.tab', has_text='客户').first.click()
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('线索')", timeout=10000)

    body = pg.inner_text("body")
    checks = {
        "获客指引标题": "获客指引" in body,
        "怎么办": "怎么办" in body,
        "风险在哪": "风险在哪" in body,
        "私信限额提示": "私信限额" in body,
        "导流词红线": "导流词" in body,
        "被动回复优先": "被动回复优先" in body,
        "去联系按钮": "去联系" in body,
        "无话术提示残留": "私信话术模块开发中" not in body,
    }
    for k, v in checks.items():
        lines.append(f"{k}: {'OK' if v else 'MISSING'}")
    pg.locator(".guide-head").click()
    pg.wait_for_timeout(300)
    body2 = pg.inner_text("body")
    lines.append(f"折叠后隐藏: {'OK' if '导流词' not in body2 else 'MISSING'}")
    pg.close()
    b.close()
assert all("OK" in l for l in lines), "获客指引断言未全通过"
print("\n".join(lines))
