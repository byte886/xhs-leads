# -*- coding: utf-8 -*-
"""F9 发布前质检 UI 自动化回归：违规词检测 / 发布强确认 / 红线不破（取消即不真发）"""
from playwright.sync_api import sync_playwright
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db'
def logs_count():
    return sqlite3.connect(DB).execute('select count(*) from publish_logs').fetchone()[0]

base_logs = logs_count()
res = []
dialogs = []


with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.contexts[0].new_page()
    pg.set_viewport_size({'width':1360,'height':920})
    pg.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto('http://127.0.0.1:8791/?stage=pick')
    pg.wait_for_timeout(1200)

    # 进创作 Tab
    pg.click('.flow .tab >> nth=1')
    pg.wait_for_timeout(800)

    # AI 工具条：未配置 Key → 按钮灰态
    ai_disabled = pg.evaluate("[...document.querySelectorAll('.ai-btn')].filter(b => !b.innerText.includes('设置')).every(b => b.disabled)")
    res.append('AI_BTN_DISABLED=' + str(ai_disabled))

    # ===== 场景 A：违规词（导流词"微信" + 绝对化"最好"）→ 质检面板命中 =====
    pg.fill('.publish input[maxlength="50"]', '南红玛瑙手串最好 加微信看货')
    pg.fill('.publish textarea', '这是一段超过五十字的正文内容，用来测试质检的结构检查功能是否正常工作，测试文字测试文字测试文字测试文字测试文字。')
    pg.fill('.publish input[placeholder^="#"]', '#南红 #手串')
    pg.click('.qc-head'); pg.wait_for_timeout(300)
    qc_text = pg.evaluate("document.querySelector('.qc-body')?.innerText || ''")
    res.append('QC_HIT_DRAIN=' + str('导流词' in qc_text and '微信' in qc_text))
    res.append('QC_HIT_ABS=' + str('绝对化用语' in qc_text and '最好' in qc_text))
    res.append('QC_HIT_STRUCT=' + str('结构' in qc_text and ('分段' in qc_text or '过短' in qc_text)))
    res.append('QC_BADGE=' + pg.evaluate("document.querySelector('.qc-badge')?.innerText || ''"))

    # 点发布 → 应弹强确认（导流词/绝对化）→ 我们 dismiss（取消）
    pg.click('.pub-btns .btn-pub')
    pg.wait_for_timeout(600)
    res.append('DIALOG_A_COUNT=' + str(len(dialogs)))
    res.append('DIALOG_A_MSG=' + (dialogs[-1][:40] if dialogs else 'NONE'))

    # ===== 场景 B：合规内容（无违规词+分段+长文）→ 提示级确认（图片二创/原创） =====
    dialogs.clear()
    pg.fill('.publish input[maxlength="50"]', '南红玛瑙手串挑选攻略')
    pg.fill('.publish textarea', '第一段：讲清买南红玛瑙手串前先看种水。\n\n第二段：颜色浓淡影响价值。\n\n第三段：种老水头足才值得入手，新手避坑记住这三点。')
    pg.fill('.publish input[placeholder^="#"]', '#南红 #珠宝')
    pg.wait_for_timeout(300)
    pg.click('.pub-btns .btn-pub')
    pg.wait_for_timeout(600)
    res.append('DIALOG_B_COUNT=' + str(len(dialogs)))
    res.append('DIALOG_B_MSG=' + (dialogs[-1][:50] if dialogs else 'NONE'))

    after_logs = logs_count()
    res.append('LOGS_DELTA=' + str(after_logs - base_logs) + ' (红线：必须=0)')
    pg.screenshot(path='/tmp/proto_v15_qc_test.png', full_page=False)
    pg.close(); b.close()


print('\n'.join(res))
