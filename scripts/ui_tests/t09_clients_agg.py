# -*- coding: utf-8 -*-
from pathlib import Path
import sqlite3
if not sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute('select count(*) from clients').fetchone()[0]:
    print('SKIP: 无客户数据（clients 为空，需先「采集客户」）')
    raise SystemExit(2)
from playwright.sync_api import sync_playwright

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
    # 属地断言改为动态：渲染「属地分布 TOP」中实际存在的省份（不再写死具体省，避免数据耦合）
    provs = [r[0] for r in sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute('select distinct ip_location from clients where ip_location != ""').fetchall()]
    top_hit = any(p in body for p in provs) if provs else ("属地分布 TOP" in body)
    checks = {
        "意向分布": "意向分布" in body,
        "属地分布TOP": "属地分布 TOP" in body,
        "近7天产出": "近 7 天产出" in body,
        "高意向2": "高意向" in body and "2" in body,
        "上意向": "上意向" in body,
        "中意向": "中意向" in body,
        "低意向": "低意向" in body,
        "属地渲染": top_hit,
        "线索总数": "线索总数" in body,
        "留联系方式命中": "留联系方式命中" in body,
    }
    for k, v in checks.items():
        print(k, "OK" if v else "MISSING")
    assert all(v for v in checks.values()), "客户聚合视图断言未全通过"
    pg.close()
    b.close()
