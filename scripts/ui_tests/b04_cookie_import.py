# -*- coding: utf-8 -*-
"""b04 Cookie 导入补测（连 9333 headless）。

覆盖清单用例：B8 Cookie 导入成功/失败分支。
- 空输入 → toast「请先粘贴 Cookie 文本」
- 无效文本 → toast「Cookie 无效或已过期」（校验失败引导扫码）
- 有效文本（取 data/cookies.json 现役 Cookie 构造 name=value 串）→ 校验通过 → toast「Cookie 有效」
前置：8791；9333 headless；引擎登录态在 data/cookies.json（已由 refresh_cookie 落盘）。
"""
import os, json, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

# 仓库重构（xhs-leads→codex-zhuke）后取可移植路径：脚本向上 2 级 = 仓库根
ROOT = Path(__file__).resolve().parents[2]

def api_try(cookie_text):
    req = urllib.request.Request('http://127.0.0.1:8791/api/import_cookie',
                                 data=json.dumps({'cookie_text': cookie_text}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')

# ---- 后端层三分支（权威断言） ----
s1, j1 = api_try('')
print('1) 空输入 ->', s1, j1.get('detail', '')[:30])
assert s1 == 400

s2, j2 = api_try('garbage_cookie=xxxx; invalid=yyyy')
print('2) 无效文本 ->', s2, (j2.get('detail') or j2.get('error') or '')[:40])
assert s2 in (400,) or j2.get('ok') is False

with open(os.path.join(ROOT, 'data/cookies.json'), encoding='utf-8') as f:
    ck = json.load(f)['cookies']
text = '; '.join(f"{c['name']}={c['value']}" for c in ck if c.get('name') and c.get('value'))
s3, j3 = api_try(text)
print('3) 有效文本 ->', s3, '| ok:', j3.get('ok'), '| count:', j3.get('count'), '| account:', j3.get('account', '')[:16])
assert s3 == 200 and j3.get('ok') is True and j3.get('count', 0) > 0

# ---- UI 层：空输入 + 无效文本提示 ----
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None) or ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('button:has-text("导入 Cookie"), .c-btn:has-text("导入")', timeout=12000)
    # 打开导入区
    if pg.locator('button:has-text("导入 Cookie")').count():
        pg.click('button:has-text("导入 Cookie")')
        pg.wait_for_timeout(600)
    ta = pg.locator('textarea[placeholder*="粘贴 Cookie"]')
    imp_btn = pg.locator('button:has-text("导入")').last
    # 4) 空输入
    imp_btn.click()
    pg.wait_for_timeout(600)
    body = pg.inner_text('body')
    print('4) 空输入 toast:', '请先粘贴 Cookie' in body)
    assert '请先粘贴 Cookie' in body
    # 5) 无效文本（后端独立 profile 校验 ~10-25s，轮询结果区/toast）
    ta.fill('garbage_cookie=xxxx; invalid=yyyy')
    imp_btn.click()
    invalid_hint = False
    for _ in range(40):
        pg.wait_for_timeout(1000)
        body = pg.inner_text('body')
        if ('Cookie 无效' in body) or ('✘' in body) or ('未能从文本中解析' in body):
            invalid_hint = True
            break
    print('5) 无效文本提示(≤40s):', invalid_hint)
    assert invalid_hint
    b.close()

print('b04 Cookie 导入：PASS')
