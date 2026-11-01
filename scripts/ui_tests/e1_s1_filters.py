# -*- coding: utf-8 -*-
"""E1-场景1 采集页筛选真实态（连引擎调试端口 9223）。

验证：真实态 select 数量/选项 → 选排序+时间 → 已选筛选行实时更新 → collect_start 请求体完整 → 低频真实采集。
踩坑记录：
  - 真实态（可见 Chrome）比 headless 9333 多一个「采集方式」select，排序从 select[1] 起、时间在 select[3]；
  - route 拦截取 body 必须用 route.request.post_data，不能用 route.post_data；
  - 采集铁律：低频单次、关键词避开高频词（弃「翡翠手镯」换「南红玛瑙手串」），绝不连环重试。

前置：后端 8791 已启动；引擎 9223（调试端口，R 批 v1.5）已登录；数据可污染（采集落库）。
"""
import os
from playwright.sync_api import sync_playwright

req_bodies = []

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()

    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)
    pg.wait_for_selector('.view-switch', timeout=10000)

    # 1) select 数量与选项
    sel_count = pg.locator('select').count()
    print("SELECTS:", sel_count)
    for i in range(sel_count):
        opts = pg.locator('select').nth(i).evaluate("el => [...el.options].map(o => o.text).join('/')")
        print(f"  select[{i}]:", opts[:80])

    # 2) 选排序=最新、时间=一周内（排序在 select[1]，时间在 select[3]）
    pg.select_option('select >> nth=1', '最新')
    pg.select_option('select >> nth=3', '一周内')
    pg.wait_for_timeout(500)
    hint = pg.evaluate("""() => {
        const h = document.querySelector('.filters-hint-v');
        return h ? h.innerText : (document.body.innerText.match(/已选筛选[^\\n]*/) || ['(无)'])[0];
    }""")
    print("已选筛选行:", hint.strip()[:80])

    # 3) 监听 collect_start 请求体 → 点开始采集（低频小量）
    def on_req(route):
        req_bodies.append(route.request.post_data or '')
        route.continue_()
    pg.route('**/api/collect_start', on_req)

    # 关键词输入框（placeholder 含"输入关键词"）+ 上限 number 输入框（产品迭代：上限从 select 改为 number）
    kw = pg.locator('input[placeholder*="关键词"]').first
    kw.fill('南红玛瑙手串')
    limit_in = pg.locator('input[type="number"]').first
    limit_in.fill('15')
    pg.locator('button', has_text='开始采集').first.click()
    print("CLICKED 开始采集")
    pg.wait_for_timeout(3000)
    if req_bodies:
        print("COLLECT_START BODY:", req_bodies[-1])
    else:
        print("COLLECT_START BODY: (未捕获)")
    b.close()
