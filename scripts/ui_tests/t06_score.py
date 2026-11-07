# -*- coding: utf-8 -*-
from pathlib import Path
import sqlite3
if not sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute('select count(*) from notes').fetchone()[0]:
    print('SKIP: 无测试数据（notes 为空，需先采集或 seed 评分数据）')
    raise SystemExit(2)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.card', timeout=15000)
    pg.wait_for_timeout(800)
    # 1. 卡片分数渲染
    badges = pg.evaluate("() => [...document.querySelectorAll('.score-badge')].map(x => x.innerText)")
    print("SCORE_BADGES:", badges)
    # 2. 点击分数 → 评分说明弹窗（hover 预览已移除，统一走弹窗）
    pg.evaluate("() => { const s=document.querySelector('.card .score-badge'); if(s) s.click() }")
    pg.wait_for_selector('.score-modal', timeout=8000)
    modal = pg.evaluate("() => document.querySelector('.score-modal').innerText")
    print("MODAL_OK:", '评分说明' in '' or ('点赞粉丝比' in modal and '互动健康度' in modal and '时效性' in modal and '低粉权重' in modal))
    print("MODAL_KEYS:", [k for k in ['点赞粉丝比','低粉权重','互动健康度','时效性','点赞','粉丝','发布','低粉爆款'] if k in modal])
    # 截图
    pg.screenshot(path='/tmp/score_modal_check.png')
    print("SHOT: /tmp/score_modal_check.png")
    pg.close(); b.close()
