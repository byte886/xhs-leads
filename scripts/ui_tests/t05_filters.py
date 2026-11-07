# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright

res = []
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.contexts[0].new_page()
    pg.set_viewport_size({'width':1360,'height':920})
    pg.goto('http://127.0.0.1:8791/?stage=pick')
    pg.wait_for_timeout(1200)
    def js(q):
        return pg.evaluate("(document.querySelector(%s)?.innerText) || null" % json_dumps(q))
    import json as _j
    json_dumps = _j.dumps
    res.append('DEFAULT=' + str(js('.filters-hint-none')))
    # 「采集方式」行已删除；展开后顺序为 排序/时间/类型/范围/距离/可见性/账号：排序 nth=0，时间 nth=1
    pg.select_option('select >> nth=0', '最新')
    pg.select_option('select >> nth=1', '一周内')
    pg.wait_for_timeout(300)
    res.append('HINT=' + str(js('.filters-hint-v')))
    pg.screenshot(path='/tmp/proto_v15_filters.png')
    pg.close(); b.close()

print('\n'.join(res))
