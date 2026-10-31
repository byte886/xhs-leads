# -*- coding: utf-8 -*-
"""E1-场景2 克隆 → 创作页（连 9333 headless）。

验证：按 badge-note 图文徽标选卡（铁律：不能按卡片 class，视频卡不带 video class）→ hover 克隆按钮（非 off 态）→
点击 → /api/clone 请求 → 创作 tab 自动激活 → 标题/图片渲染（异步补图 15-25s）。
踩坑记录：
  - 选图文卡必须按 .badge-note 徽标文本（图文/视频），不能按卡片 class（视频卡 class 不带 video，按钮带 off class + title 拦截是产品行为正确）；
  - 切 Tab 必须原生 click，严禁 evaluate DOM click；goto 后必须等 .flow-hint 含「关键词采集」稳定 +500ms 再等 .view-switch。

前置：后端 8791；9333 headless + 小红书已登录；有已采集图文卡（数据依赖）。
"""
from playwright.sync_api import sync_playwright

reqs, errs = [], []

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')  # R 批 v1.5：连引擎调试端口（引擎带 --remote-debugging-port=9223；旧外部 Chrome 测试路径退役）
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()

    pg.on('request', lambda r: reqs.append(r.url))
    pg.on('requestfailed', lambda r: errs.append(f"{r.url} :: {r.failure}"))

    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)
    pg.wait_for_selector('.view-switch', timeout=10000)
    pg.wait_for_selector('.card', timeout=15000)

    card = pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).first
    print("选中卡 badge:", card.locator('.badge-note').inner_text(),
          "| title:", card.locator('.title, .t').first.inner_text()[:30] if card.locator('.title, .t').count() else '')

    card.hover()
    pg.wait_for_timeout(600)
    btn = card.locator('button.ha-btn.clone').first
    print("按钮 off class:", 'off' in (btn.get_attribute('class') or ''))
    btn.click()
    print("CLICKED")

    pg.wait_for_timeout(15000)
    clones = [u for u in reqs if '/api/clone' in u]
    print("CLONE_REQ:", clones)
    create_tab = pg.locator('.tab', has_text='创作').first
    print("创作tab class:", create_tab.get_attribute('class'))
    tin = pg.locator('input[maxlength="50"]').first  # 标题框（踩坑：用 maxlength 勿用 placeholder 匹配）
    print("标题值:", tin.input_value()[:40] if tin.count() else '(无输入框)')
    imgs = pg.locator('.pub-img img')  # 图片容器 .pub-imgs > .pub-img > img
    print("PUB_IMAGES(15s):", imgs.count())
    pg.wait_for_timeout(10000)
    print("PUB_IMAGES(25s):", pg.locator('.pub-img img').count())
    print("失败:", errs[:3])
    b.close()
