# -*- coding: utf-8 -*-
"""v1.5 4-Tab 工作流 UI 自动化验证（CDP 9333 + ?stage=pick）"""
from playwright.sync_api import sync_playwright

res = []
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.contexts[0].new_page()
    pg.set_viewport_size({'width':1360,'height':920})
    pg.goto('http://127.0.0.1:8791/?stage=pick')
    pg.wait_for_timeout(1200)

    # 1) 4-Tab 导航 + 全局状态条
    tabs = pg.query_selector_all('.flow .tab')
    res.append('TAB_COUNT=' + str(len(tabs)))
    res.append('TABS=' + '|'.join(t.inner_text().replace('\n','').strip() for t in tabs))
    sys_items = [s.inner_text().strip() for s in pg.query_selector_all('.sys-item')]
    res.append('SYSBAR=' + ';'.join(sys_items))

    # 2) 创作 Tab：PublishPage + AI 工具条 + 质检卡
    pg.click('.flow .tab >> nth=1')  # 创作
    pg.wait_for_timeout(800)
    res.append('CREATE_TITLE=' + (pg.evaluate("document.querySelector('.publish h2')?.innerText || 'MISSING'")))
    ai_btns = pg.query_selector_all('.ai-bar .ai-btn')
    res.append('AI_BTN_COUNT=' + str(len(ai_btns)))
    res.append('QC_CARD=' + ('OK' if pg.query_selector('.qc-card') else 'MISSING'))
    # 质检展开
    pg.click('.qc-head'); pg.wait_for_timeout(300)
    res.append('QC_BODY=' + str(pg.evaluate("document.querySelector('.qc-body') !== null")))

    # 3) 设置 Tab：三卡
    pg.click('.flow .tab >> nth=3')  # 设置
    pg.wait_for_timeout(800)
    cards = [c.inner_text().split('\n')[0].strip() for c in pg.query_selector_all('.set-card .set-card-h')]
    res.append('SET_CARDS=' + ';'.join(cards))

    # 4) 客户 Tab
    pg.click('.flow .tab >> nth=2')  # 客户
    pg.wait_for_timeout(800)
    res.append('CLIENTS_TAB=' + (pg.evaluate("document.querySelector('.cl-empty-wrap .big')?.innerText || document.querySelector('.info-bar')?.innerText || 'MISSING'") or ''))

    # 5) 回采集 Tab：WorkArea 笔记视图
    pg.click('.flow .tab >> nth=0')  # 采集
    pg.wait_for_timeout(800)
    res.append('COLLECT_VIEW=' + ('notes-view' if pg.query_selector('.view-switch') else 'MISSING'))

    pg.screenshot(path='/tmp/proto_v15_app.png', full_page=False)
    pg.close(); b.close()

print('\n'.join(res))
