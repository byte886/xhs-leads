# -*- coding: utf-8 -*-
"""b02 停止采集补测（连 9333 headless）。

覆盖清单用例：B4 采集中再点「停止采集」→ 请求停止 → 任务收尾（非 force kill）。
低频铁律：关键词用「南红玛瑙手串」，limit 小、interval 大，采集中手动停止，不连环重试。
前置：8791 已启动；9333 headless 已启动且已登录。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('input[placeholder*="关键词"]', timeout=12000)

    # 填参数：低频小任务 + 慢速（给停止留窗口）
    pg.fill('input[placeholder*="关键词"]', '南红玛瑙手串')
    # limit 输入框（placeholder 含结果上限/数量）
    limit_input = pg.locator('input[placeholder*="上限"], input[placeholder*="数量"]')
    if limit_input.count():
        limit_input.first.fill('5')
    # 间隔输入框（若有）设为 3000ms 慢速
    interval_input = pg.locator('input[placeholder*="间隔"], input[placeholder*="interval"]')
    if interval_input.count():
        interval_input.first.fill('3000')

    # 开始采集
    pg.click('button:has-text("开始采集"), button:has-text("开始采集")')
    pg.wait_for_timeout(1500)
    collecting = pg.locator('.collecting, [class*="collecting"], button:has-text("停止采集")').count()
    print('1) 采集中态出现:', collecting > 0)
    if not collecting:
        # 采集可能已完成或按钮文案不同——检查当前状态文本
        note = pg.inner_text('body').split('采集中')[0][-80:] if '采集中' in pg.inner_text('body') else ''
        print('   状态片段:', pg.inner_text('body')[:120].replace('\n', ' | '))

    # 点停止（同一按钮再点 = 停止）
    stop_btn = pg.locator('button:has-text("停止采集")')
    if stop_btn.count() == 0:
        stop_btn = pg.locator('button:has-text("开始采集")')
    stop_btn.first.click()
    pg.wait_for_timeout(2500)

    body = pg.inner_text('body')
    stopped_hint = ('已请求停止' in body) or ('停止' in body and '采集中' not in body)
    print('2) 停止提示/状态回落:', stopped_hint)

    # 采集状态应不再显示「采集中」
    collecting_after = '采集中' in pg.inner_text('body')
    print('3) 停止后无采集中态:', not collecting_after)

    assert stopped_hint and not collecting_after, '停止采集未生效'
    print('b02 停止采集：PASS')
