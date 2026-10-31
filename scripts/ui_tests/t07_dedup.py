# -*- coding: utf-8 -*-
from pathlib import Path
import sqlite3
if not sqlite3.connect(Path(__file__).resolve().parents[2] / 'data' / 'xhs_notes.db').execute("select count(*) from drafts where status='draft'").fetchone()[0]:
    print('SKIP: 无草稿数据（需先保存含重复图 URL 的草稿）')
    raise SystemExit(2)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    pg = b.new_page()
    pg.goto('http://127.0.0.1:8791/', timeout=20000)
    pg.wait_for_selector('.tab', timeout=15000)
    pg.wait_for_function("document.querySelector('.flow-hint')?.innerText.includes('关键词采集')", timeout=15000)
    pg.wait_for_timeout(500)
    pg.locator('.tab', has_text='创作').first.click()
    pg.wait_for_selector('button.btn-draft', timeout=15000)
    pg.evaluate("() => { const b=[...document.querySelectorAll('button.btn-draft')].find(x=>x.innerText.includes('草稿库')); if(b) b.click(); }")
    pg.wait_for_selector('.draft-item', timeout=10000)
    # 点「去重测试」草稿（含 4 个重复 URL）
    pg.evaluate("() => { const d=[...document.querySelectorAll('.draft-item')].find(x=>x.innerText.includes('去重测试')); if(d) d.click(); }")
    pg.wait_for_timeout(1500)
    title_now = pg.evaluate("() => { const i=document.querySelector('.pub-title input, input[placeholder*=标题]'); return i ? i.value : '?' }")
    n = pg.evaluate("() => document.querySelectorAll('.pub-img img').length")
    imgs = pg.evaluate("() => [...document.querySelectorAll('.pub-img img')].map(i => i.src)")
    print("TITLE:", title_now)
    print("IMG_COUNT:", n, "(期望 2)")
    for s in imgs: print("  -", s[:100])
    pg.close(); b.close()
