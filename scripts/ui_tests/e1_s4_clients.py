# -*- coding: utf-8 -*-
"""E1-场景4 真实采集客户（连 9333 headless）。

验证：hover 图文卡 → 「采集客户」按钮 → 点击 → 采集互动用户（真实访问小红书详情页 30-90s）→ 自动切客户 tab。
踩坑记录：
  - 采集客户走浏览器全局锁低频（真实访问页面，切勿连环触发）；INTENT_LABEL 4 档意向；
  - 低互动笔记（评论 1-4 条）可能首批采集为空——属正常，换评论最多的卡重采；
  - 30s 后只验证是否已切客户 tab，完整结果（线索列表/意向/属地/获客指引/CSV）见 /tmp/e1_s4_clients_ui 脚本与完成记录。

前置：后端 8791；9333 headless + 已登录；有已采集图文卡（带评论互动）；风控窗口内低频执行。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)

    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)
    pg.wait_for_selector('.view-switch', timeout=10000)

    card = pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).first
    card.hover()
    pg.wait_for_timeout(500)
    client_btn = card.locator('button.ha-btn.client').first
    print("采集客户按钮可见:", client_btn.is_visible())
    client_btn.click()
    print("已点击采集客户")

    # 轮询等待（采集真实访问页面 30-90s，固定等待不可靠）：最多 90s，每 5s 查当前 tab
    import time
    t0 = time.time()
    cur_tab = '(未知)'
    while time.time() - t0 < 90:
        pg.wait_for_timeout(5000)
        cur_tab = pg.evaluate("""() => {
            const t = [...document.querySelectorAll('.tab')];
            const cur = t.find(x => x.className.includes('cur'));
            return cur ? cur.innerText.trim() : '(无)';
        }""")
        if '客户' in cur_tab:
            break
    print("最终当前Tab:", cur_tab)
    b.close()
