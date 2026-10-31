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
    pg.wait_for_selector(".tab", timeout=15000)
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('关键词采集')", timeout=15000)
    pg.wait_for_timeout(500)
    pg.locator('.tab', has_text='客户').first.click()
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('线索')", timeout=10000)

    pg.wait_for_timeout(1200)
    body = pg.inner_text("body")
    dbc = sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db')
    provs = [r[0] for r in dbc.execute('select distinct ip_location from clients where ip_location != ""').fetchall()]
    has_hi = dbc.execute('select count(*) from clients where intent="high"').fetchone()[0] > 0
    checks = {
        "线索总数": "线索总数" in body,
        "留联系方式命中": "留联系方式命中" in body,
        "导出线索CSV": "导出线索 CSV" in body,
        "高意向标签": "高意向" in body,
        "上意向标签": "上意向" in body,
        "中意向标签": "中意向" in body,
        "低意向标签": "低意向" in body,
        "属地渲染": any(p in body for p in provs) if provs else True,
        "命中词列": "命中" in body,
        "去联系按钮": "去联系" in body,
        "主页链接": "主页 ↗" in body,
        "近7天tab": "近 7 天" in body,
    }
    for k, v in checks.items():
        print(k, "OK" if v else "MISSING")
    i_hi = body.find("高意向"); i_low = body.find("低意向")
    if has_hi:
        print("高意向置顶:", "OK" if 0 < i_hi < i_low else "FAIL")
        assert 0 < i_hi < i_low, "存在 high 客户但未置顶"
    else:
        print("高意向置顶: SKIP（当前无 high 客户，仅验证标签渲染）")
    assert all(v for v in checks.values()), "客户线索列表断言未全通过"
    pg.close()
    b.close()
