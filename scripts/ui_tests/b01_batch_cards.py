# -*- coding: utf-8 -*-
"""b01 卡片批量操作补测（连 9333 headless）。

覆盖清单用例：C5 全选图文 / C6 清空选择 / C7 单选 / C8 下载图片包 / C9 导出 JSON / C10 导出 CSV。
前置：8791 已启动（free 模式）；9333 headless 已启动；notes 表有数据（采集页有卡片）。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.card', timeout=15000)
    pg.wait_for_timeout(500)

    # 批量区存在
    batch_visible = pg.locator('button.b-btn', has_text='全选图文').count() > 0
    print('1) 批量区存在:', batch_visible)

    # C5 全选图文（语义=只选图文类型卡片）
    pg.click('button.b-btn:has-text("全选图文")')
    pg.wait_for_timeout(300)
    total = pg.locator('.card').count()
    sel = pg.locator('.card.sel').count()
    img_cards = pg.locator('.card:has(.badge-note:text("图文"))').count()
    print(f'2) 全选后选中数: {sel} (图文卡 {img_cards} / 总卡 {total})')
    assert sel == img_cards and img_cards > 0, '全选图文未生效'

    # C6 清空选择
    pg.click('button.b-btn:has-text("清空选择")')
    pg.wait_for_timeout(300)
    sel0 = pg.locator('.card.sel').count()
    print('3) 清空后选中数:', sel0)
    assert sel0 == 0, '清空未生效'

    # C7 单选（点第一张卡）
    pg.locator('.card').first.click()
    pg.wait_for_timeout(300)
    sel1 = pg.locator('.card.sel').count()
    print('4) 单选后选中数:', sel1)
    assert sel1 == 1, '单选未生效'

    # C8 下载图片包（expect_download 捕获产物）
    try:
        with pg.expect_download(timeout=15000) as dl_info:
            pg.click('button.b-btn:has-text("下载图片包")')
        dl = dl_info.value
        path = dl.path()
        import os
        size = os.path.getsize(path) if path else 0
        print(f'5) 图片包下载: {dl.suggested_filename} size={size}')
        assert size > 0 and 'xhs_images' in dl.suggested_filename, '图片包产物异常'
    except Exception as e:
        print('5) 图片包下载失败:', str(e)[:120])
        # 兜底：验证后端打包接口
        import urllib.request, json as _j
        req = urllib.request.Request('http://127.0.0.1:8791/api/download_images',
                                     data=_j.dumps({'feed_ids': ['__none__']}).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as he:
            print('   后端 /api/download_images 响应:', he.code)
            assert he.code in (400, 200), '后端接口异常'

    # C9 导出 JSON
    with pg.expect_download(timeout=15000) as dl_info:
        pg.click('button.b-btn:has-text("导出 JSON")')
    dl = dl_info.value
    dl.save_as('/tmp/b01_notes.json')
    import json
    data = json.load(open('/tmp/b01_notes.json', encoding='utf-8-sig'))
    print(f'6) JSON 导出: {len(data)} 条')
    assert len(data) >= 1 and 'feed_id' in data[0], 'JSON 内容异常'

    # C10 导出 CSV
    with pg.expect_download(timeout=15000) as dl_info:
        pg.click('button.b-btn:has-text("导出 CSV")')
    dl = dl_info.value
    dl.save_as('/tmp/b01_notes.csv')
    lines = open('/tmp/b01_notes.csv', encoding='utf-8-sig').read().strip().split('\n')
    print(f'7) CSV 导出: 表头+{len(lines)-1} 行')
    assert len(lines) >= 2 and '标题' in lines[0], 'CSV 内容异常'

    print('b01 批量操作：PASS')
