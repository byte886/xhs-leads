# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.new_page()
    pg.goto('http://127.0.0.1:8791/', timeout=20000)
    pg.wait_for_selector('.tab', timeout=15000)
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('关键词采集')", timeout=15000)
    pg.wait_for_timeout(500)
    pg.locator('.tab', has_text='创作').first.click()
    pg.wait_for_selector('.ai-bar', timeout=10000)
    assert pg.locator('.ai-bar').first.is_visible(), '创作 Tab 未真实切换（.ai-bar 不可见）'
    assert '克隆 + AI 文稿' in pg.inner_text('body'), '创作 hint 未出现'
    btns = pg.evaluate("() => [...document.querySelectorAll('.ai-bar .ai-btn')].map(b => b.innerText.trim() + (b.disabled ? '(灰)' : ''))")
    print("AI_BUTTONS:", btns)
    pg.close(); b.close()
