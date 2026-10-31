# -*- coding: utf-8 -*-
"""UI 自动化：发布记录区展开自动加载 + 回写结果展示（sync）。"""
from pathlib import Path
import sqlite3
if not sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute('select count(*) from publish_logs').fetchone()[0]:
    print('SKIP: 无发布日志（publish_logs 为空，需先发布/存草稿）')
    raise SystemExit(2)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.tab', timeout=15000)
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('关键词采集')", timeout=15000)
    pg.wait_for_timeout(500)
    pg.locator('.tab', has_text='创作').first.click()
    pg.wait_for_selector('.pub-log-panel summary', timeout=10000)
    print('summary:', pg.locator('.pub-log-panel summary').inner_text().strip())
    pg.locator('.pub-log-panel summary').click()
    pg.wait_for_timeout(1500)
    n = pg.locator('.pub-log-item').count()
    print('log items:', n)
    if n:
        first = pg.locator('.pub-log-item').nth(0).inner_text().replace('\n', ' | ')
        print('first:', first[:200])
        body = pg.locator('body').inner_text()
        assert '赞 12' in body, '回写结果未展示'
        assert '浏览' in body, '浏览量列未展示'
        print('PASS: 发布记录区 + 展开自动加载 + 回写结果展示正常')
    else:
        print('FAIL: 展开后仍无记录')
    pg.close(); b.close()
