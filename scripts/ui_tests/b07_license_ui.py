# -*- coding: utf-8 -*-
"""b07 授权 UI 补测：free 状态条 + activation 激活流程（连 9333 headless）。

覆盖清单用例：F5 授权状态条(free) / F6 激活流程 / F7 激活错误码 / G1 license_status / G2 license_activate。
前置：8791 free 实例 + 8792 activation 实例均运行；/tmp/b07_code.txt 含 30 天激活码。
注意：两实例共用 data/license.json——脚本先还原未激活态，结尾还原 free 模板。
"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.expanduser('~/Desktop/xhs-leads')
CODE = open('/tmp/b07_code.txt').read().strip()
LIC_PATH = os.path.join(ROOT, 'data/license.json')

def write_license(content):
    with open(LIC_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

def api_activate(port, code):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/license_activate',
                                 data=json.dumps({'key': code}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')

def license_mode(port):
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/api/license_status', timeout=8))['status']['mode']

# 预置：还原未激活（activation 实例读 free 模板 → 未激活态）
write_license('{"schema_version":1,"mode":"free"}')

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9333')
    ctx = b.contexts[0]

    # ---- F5 free 实例 8791 ----
    pg1 = next((x for x in ctx.pages if '8791' in (x.url or '')), None) or ctx.new_page()
    pg1.goto(f'http://127.0.0.1:8791/?stage=pick&v={int(time.time())}', timeout=20000)
    pg1.wait_for_selector('.tab', timeout=10000)
    pg1.locator('.tab', has_text='设置').first.click()
    pg1.wait_for_selector('.lic-bar, [class*="lic-bar"]', timeout=12000)
    lic_text = pg1.locator('.lic-bar, [class*="lic-bar"]').first.inner_text()
    print('1) free 状态条:', lic_text[:60].replace('\n', ' | '))
    assert '免费版' in lic_text, 'free 未显示免费版标签'
    no_act = pg1.locator('input[placeholder*="激活码"]').count() == 0
    print('2) free 无激活输入区:', no_act)
    assert no_act, 'free 模式不应显示激活区'

    # ---- F6/F7 activation 实例 8792（未激活 → 坏码 → 真码） ----
    pg2 = next((x for x in ctx.pages if '8792' in (x.url or '')), None) or ctx.new_page()
    pg2.goto(f'http://127.0.0.1:8792/?stage=pick&v={int(time.time())}', timeout=20000)
    pg2.wait_for_selector('.tab', timeout=10000)
    pg2.locator('.tab', has_text='设置').first.click()
    pg2.wait_for_selector('.lic-bar, [class*="lic-bar"]', timeout=12000)
    lic2 = pg2.locator('.lic-bar, [class*="lic-bar"]').first.inner_text()
    print('3) activation 未激活状态条:', lic2[:60].replace('\n', ' | '))
    inp = pg2.locator('input[placeholder*="激活码"], input[placeholder*="激活"]').first
    print('4) activation 有激活输入区:', inp.count() > 0)
    assert inp.count() > 0, 'activation 模式应显示激活区'

    # 坏码 → 失败提示
    inp.fill('bad_code_xxx')
    pg2.locator('button:has-text("激活")').first.click()
    pg2.wait_for_timeout(2000)
    body2 = pg2.inner_text('body')
    bad_hint = '无效' in body2 or '失败' in body2
    print('5) 坏码提示:', bad_hint)
    assert bad_hint, '坏码未提示失败'

    # 真码 → 激活版 + 已激活（输入区消失）
    # 坏码 toast 可能触发页面状态刷新，重新定位输入区（必要时重进设置 tab）
    pg2.wait_for_timeout(1500)
    inp2 = pg2.locator('input[placeholder*="激活码"]').first
    if inp2.count() == 0 or not inp2.is_visible():
        pg2.locator('.tab', has_text='设置').first.click()
        pg2.wait_for_timeout(1500)
        inp2 = pg2.locator('input[placeholder*="激活码"]').first
    inp2.fill(CODE)
    pg2.locator('button:has-text("激活")').first.click()
    pg2.wait_for_timeout(2500)
    lic2b = pg2.locator('.lic-bar, [class*="lic-bar"]').first.inner_text()
    print('6) 激活后状态条:', lic2b[:80].replace('\n', ' | '))
    assert '已激活' in lic2b, '激活后未显示已激活'
    print('7) 激活后输入区消失:', pg2.locator('input[placeholder*="激活码"]').count() == 0)
    b.close()

# ---- G2 接口层：坏码 400 / 真码 200 ----
s_bad, j_bad = api_activate(8792, 'bad_code_xxx')
print('8) 接口坏码 ->', s_bad, str(j_bad)[:60])
assert s_bad == 400
s_ok, j_ok = api_activate(8792, CODE)
print('9) 接口真码 ->', s_ok, '| ok:', j_ok.get('ok'))
assert s_ok == 200 and j_ok.get('ok')

# ---- 清理：还原 free 模板（避免污染 8791/8792 后续使用） ----
write_license('{"schema_version":1,"mode":"free"}')
print('10) 8791 仍 free:', license_mode(8791))
assert license_mode(8791) == 'free'
print('b07 授权 UI：PASS')
