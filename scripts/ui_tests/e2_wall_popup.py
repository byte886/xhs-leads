# -*- coding: utf-8 -*-
"""E2 真实场景验证：登录墙「登录后查看」→ 采集失败 note → 前端 ForceLogin 弹框
（R-6.21e/f/g 交互驱动 + 精确词集在真实平台文案下的端到端验证）"""
import json, time, urllib.request
from playwright.sync_api import sync_playwright

def collect_state():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8791/api/collect_progress', timeout=5) as r:
            return json.load(r)
    except Exception:
        return {}

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('input[placeholder*="关键词"]', timeout=12000)
    pg.fill('input[placeholder*="关键词"]', '南红玛瑙手串')
    li = pg.locator('input[placeholder*="上限"], input[placeholder*="数量"]')
    if li.count(): li.first.fill('5')
    pg.click('button:has-text("开始采集")')

    got_wall_note = False
    got_popup = False
    last_note = ''
    for i in range(36):  # 最长 ~70s（登录墙检测窗口 45s + 轮询余量）
        time.sleep(2)
        st = collect_state()
        note = (st.get('note') or '')
        if note and note != last_note:
            last_note = note
        if '登录后查看' in note or '需人工扫码' in note:
            got_wall_note = True
        # 前端弹框（force-mask 挂载即弹）
        try:
            if pg.locator('.force-mask').count() > 0 and pg.locator('.force-mask').first.is_visible():
                got_popup = True
                pg.screenshot(path='/tmp/e2_popup.png')
                mask_text = pg.locator('.force-box').inner_text().strip().replace('\n', ' | ')[:120]
                break
        except Exception:
            pass
        if got_wall_note and i > 12:
            break
    print('1) 后端采集失败 note 命中登录墙:', got_wall_note)
    print('   note:', last_note[:130])
    print('2) 前端 ForceLogin 弹框出现:', got_popup)
    if got_popup:
        print('   弹框文案:', mask_text)
    assert got_wall_note and got_popup, '真实登录墙未触发弹框'
    print('E2 真实场景弹框验证：PASS')
