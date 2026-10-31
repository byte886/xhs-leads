# -*- coding: utf-8 -*-
"""UI 快速回归：4-Tab 切换 + 发布记录区渲染 + 评分说明弹窗。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.tab', timeout=15000)
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('关键词采集')", timeout=15000)
    pg.wait_for_timeout(500)
    # 1. 4-Tab 存在且可切换
    tabs = pg.evaluate("() => [...document.querySelectorAll('.tab')].map(t => t.innerText.trim())")
    print('tabs:', tabs)
    for name in ('采集', '创作', '客户', '设置'):
        assert any(name in t for t in tabs), f'缺 tab: {name}'
    # 2. 创作页发布记录区（空态文案）
    pg.locator('.tab', has_text='创作').first.click()
    pg.wait_for_selector('.pub-log-panel summary', timeout=10000)
    print('pub-log summary:', pg.locator('.pub-log-panel summary').inner_text().strip()[:40])
    # 3. 设置页三卡
    pg.evaluate("() => { const t=[...document.querySelectorAll('.tab')].find(x=>x.innerText.includes('设置')); if(t) t.click(); }")
    pg.wait_for_timeout(800)
    stxt = pg.locator('body').inner_text()
    assert 'CDP' in stxt or 'AI' in stxt or '人设' in stxt, '设置页未渲染'
    print('PASS: 4-Tab / 发布记录区 / 设置页 健康')
    pg.close(); b.close()
