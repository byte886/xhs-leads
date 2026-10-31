# -*- coding: utf-8 -*-
"""b06 客户导出线索 CSV 补测（连 9333 headless）。

覆盖清单用例：E7 导出线索 CSV。
前置：8791；9333 headless；clients 表有数据（e1_s4 真实采集 10 条）。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None) or ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.tab', timeout=10000)
    # 切客户 tab
    pg.locator('.tab', has_text='客户').first.click()
    pg.wait_for_selector('.cl-item, .cl-row, [class*="cl-item"]', timeout=15000)
    cl_rows = pg.locator('.cl-item, .cl-row, [class*="cl-item"]').count()
    print('1) 客户列表条数:', cl_rows)
    assert cl_rows >= 1, '无客户数据'

    with pg.expect_download(timeout=15000) as dl_info:
        pg.evaluate("() => { const b=[...document.querySelectorAll('button')].find(x=>x.innerText.includes('导出线索 CSV')); if(b) b.click() }")
    dl = dl_info.value
    dl.save_as('/tmp/b06_clients.csv')
    import csv
    rows = list(csv.reader(open('/tmp/b06_clients.csv', encoding='utf-8-sig')))
    print(f'2) CSV: 表头 {len(rows[0])} 列, 数据 {len(rows)-1} 行 (客户 {cl_rows})')
    assert len(rows) - 1 == cl_rows, 'CSV 行数与客户数不符'
    assert rows[0][0] == '昵称' and rows[0][2] == '评论原文', '表头异常'
    print('b06 客户导出：PASS')
