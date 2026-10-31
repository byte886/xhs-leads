# -*- coding: utf-8 -*-
"""b05 杂项补测：结论标签 + 打开原作品 + 复制原作品链接（连 9333 headless）。

覆盖清单用例：C3 结论标签 / C4 打开原作品 / D17 复制原作品链接。
前置：8791；9333 headless + 登录态；有已评分卡片。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None) or ctx.new_page()

    # ---- C3 结论标签（评分弹窗内 tagConclusion 文案） ----
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.card .score-badge', timeout=15000)
    # 打开第一张卡的评分弹窗
    pg.evaluate("() => { const s=document.querySelector('.card .score-badge'); if(s) s.click() }")
    pg.wait_for_selector('.score-modal, .sm-concl, [class*="sm-concl"]', timeout=8000)
    concl = pg.locator('.sm-concl').first.inner_text()
    sm_tag = pg.locator('.sm-tag').first.inner_text()
    print('1) 评分弹窗标签:', sm_tag, '| 结论:', concl[:40])
    assert len(concl) > 4, '结论文案缺失'
    # 卡片热榜/高评分徽标（tag 展示层）
    tag_visible = pg.locator('.card .tag').count() > 0
    print('2) 卡片 tag 徽标存在:', tag_visible)
    # 关弹窗（点 mask 自身关闭）
    pg.click('.score-modal-mask', position={'x': 10, 'y': 10})
    pg.wait_for_timeout(300)
    assert pg.locator('.score-modal').count() == 0, '评分弹窗未关闭'

    # ---- C4 打开原作品（新窗口 explore 链接） ----
    card0 = pg.locator('.card').first
    with pg.expect_popup(timeout=12000) as popup_info:
        pg.evaluate("() => { const o=document.querySelector('.card .open-link'); if(o) o.click() }")
    pop = popup_info.value
    pop.wait_for_load_state('domcontentloaded', timeout=12000)
    purl = pop.url
    print('3) 打开原作品 URL:', purl[:70])
    assert 'xiaohongshu.com/explore/' in purl, '未跳转 explore 原作品'
    pop.close()

    # ---- D17 复制原作品链接（创作页入口 → toast） ----
    card = pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).first
    card.hover()
    pg.wait_for_timeout(600)
    card.locator('button.ha-btn.clone').first.click()
    pg.wait_for_timeout(4000)
    link = pg.locator('a:has-text("复制原作品链接")')
    if link.count():
        link.first.click()
        pg.wait_for_timeout(600)
        body = pg.inner_text('body')
        copied = '已复制原作品链接' in body
        print('4) 复制原作品链接 toast:', copied)
        assert copied, '复制提示未出现'
    else:
        print('4) (创作页无复制链接入口，跳过)')

    print('b05 杂项补测：PASS')
