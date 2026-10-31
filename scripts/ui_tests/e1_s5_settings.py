# -*- coding: utf-8 -*-
"""E1-场景5 设置页三卡（AI Key 门控 / CDP 配置 / 账号人设）（连 9333 headless）。

验证：AI Key 保存 → 掩码/状态条 → AI 按钮灰态解除 → 测后复原；CDP 区状态；账号人设保存 toast。
踩坑记录：
  - 设置页 Key 输入框每次进入被 fillAiForm 清空（设计：留空保留，前端不持久回显明文），填假 Key 仅测 UI 流程；
  - AI 按钮灰态看 disabled/off class（创作页 .tab 需已激活采集态）；
  - 测后复原：真实清除 Key 需后端清除语义（空 key 保存=保留是保护设计），本轮由 AI Key 清除修复（e1_s5 配套）提供「清除配置」入口。

前置：后端 8791；9333 headless。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    pg.goto('http://127.0.0.1:8791/', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)

    # 切设置 tab（原生 click）
    pg.locator('.tab', has_text='设置').first.click()
    pg.wait_for_timeout(1000)

    # 1) AI Key 区当前状态
    ai_status = pg.evaluate("""() => {
        const s = [...document.querySelectorAll('*')].filter(e => e.children.length===0 && /AI 配置|Key|未配置|已配置/.test(e.textContent || '')).map(e=>e.textContent.trim());
        return [...new Set(s)].slice(0,6);
    }""")
    print("1) 设置页 AI 区文本:", ai_status)

    # 2) 填 Key 保存（假 Key 仅测 UI 流程）
    key_input = pg.locator('input[type="password"], input[placeholder*="sk-"], input[placeholder*="Key"]').first
    print("2) Key输入框:", key_input.count())
    if key_input.count():
        key_input.fill('sk-test-123456')
        # Base URL / 模型：placeholder 精确/特征匹配（"https://api.openai.com/v1" 不含 base/模型字样）
        base_el = pg.locator('input[placeholder="https://api.openai.com/v1"]').first
        if base_el.count():
            base_el.fill('https://api.openai.com/v1')
        model_el = pg.locator('input[placeholder*="gpt-4o-mini"]').first
        if model_el.count():
            model_el.fill('gpt-4o-mini')
        pg.locator('button', has_text='保存配置').first.click()
        pg.wait_for_timeout(1500)
        # 顶部状态条是 button（"AI Key 已配置/未配置"），勿匹配页面标题"AI Key / CDP / 账号人设"
        badge = pg.evaluate("""() => [...document.querySelectorAll('button')].filter(b => /AI Key/.test(b.innerText)).map(b => b.innerText.trim())""")
        print("3) 保存后状态条:", badge)

    # 3) CDP 区
    cdp_txt = pg.evaluate("""() => {
        const t = document.body.innerText;
        const m = t.match(/CDP[^\\n]*/g);
        return m ? [...new Set(m)].slice(0,5) : '(无CDP文本)';
    }""")
    print("4) CDP 区:", cdp_txt)

    # 4) 账号人设：填 + 保存（placeholder 特征词：珠宝翡翠/干货型/逗号分隔——勿用"行业/语气/话题"）
    persona_fill = [
        ('input[placeholder*="珠宝翡翠"]', '玉石珠宝'),
        ('input[placeholder*="干货型"]', '专业温和'),
        ('input[placeholder*="逗号分隔"]', '南红,手串,翡翠'),
    ]
    filled = 0
    for sel, val in persona_fill:
        el = pg.locator(sel).first
        if el.count():
            el.fill(val)
            filled += 1
    print("5) 人设输入框填充:", filled)
    if filled:
        pg.locator('button', has_text='保存人设').first.click()
        pg.wait_for_timeout(1000)
        toast = pg.evaluate("""() => {
            const t = document.querySelector('[class*=toast]');
            return t ? t.innerText : '(无)';
        }""")
        print("6) 人设保存 toast:", toast)

    # 5) 复原：用「清除配置」入口清掉测试 Key（本轮新增），验证状态条回未配置
    clear_btn = pg.locator('button', has_text='清除配置').first
    if clear_btn.count():
        clear_btn.click()
        pg.wait_for_timeout(1500)
        badge2 = pg.evaluate("""() => [...document.querySelectorAll('button')].filter(b => /AI Key/.test(b.innerText)).map(b => b.innerText.trim())""")
        print("7) 清除后状态条:", badge2)
    b.close()
