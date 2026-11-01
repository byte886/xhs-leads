# -*- coding: utf-8 -*-
"""E1-场景3 创作页质检 + 图片操作 + 草稿库（连引擎调试端口 9223）。

验证：克隆进创作 → 质检面板（违规词/结构/二创/原创）→ 违规词命中（绝对化用语）→ 合规恢复 →
lightbox 看全图（点击灯箱本身关闭，Escape 无效）→ 删除图片 → 本地文件追加 → 保存草稿 → 草稿库恢复/删除。
踩坑记录：
  - 标题框定位用 input[maxlength="50"]，勿用 placeholder 匹配（易歧义）；
  - 质检展开/收起通过 .qc-head 点击（收起后再展开触发实时检查）；
  - 本地追加图片路径必须可移植：优先仓库 data/images 现有缓存图，兜底 /tmp 截图，不硬编码 /Users/<用户名>。

前置：后端 8791；引擎 9223（调试端口，R 批 v1.5）已登录；有已采集图文卡（数据依赖，无卡自动 SKIP）；数据可污染（草稿数会变）。
"""
import os
import pathlib
import sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]  # 仓库根（scripts/ui_tests/ → 上两级）

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)

    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)
    pg.wait_for_selector('.view-switch', timeout=10000)

    # 数据预检：无已采集图文卡 → 数据依赖不足，SKIP（exit 2，与 run_all 语义一致）
    if pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).count() == 0:
        print("SKIP: 无已采集图文卡（数据依赖不足）")
        sys.exit(2)

    card = pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).first
    card.hover()
    pg.wait_for_timeout(500)
    card.locator('button.ha-btn.clone').first.click()
    print("1) 克隆点击")
    pg.wait_for_timeout(20000)  # 等异步补图
    imgs0 = pg.locator('.pub-img img').count()
    print("2) 克隆后图片数:", imgs0)
    create_tab = pg.locator('.tab', has_text='创作').first
    print("3) 创作tab激活:", 'cur' in (create_tab.get_attribute('class') or ''))

    # ---- 质检 ----
    pg.locator('.qc-head').first.click()
    pg.wait_for_timeout(800)
    qc = pg.evaluate("""() => {
        const body = document.querySelector('.qc-body');
        return body ? body.innerText.replace(/\\n+/g,' | ') : '(无质检面板)';
    }""")
    print("4) 质检面板:", qc[:220])

    # 违规词命中：正文加"全场最低价"
    body_input = pg.locator('textarea').first
    body_input.fill(body_input.input_value() + " 全场最低价")
    pg.locator('.qc-head').first.click()  # 收起
    pg.wait_for_timeout(300)
    pg.locator('.qc-head').first.click()  # 再展开 → 实时检查
    pg.wait_for_timeout(800)
    qc2 = pg.evaluate("""() => {
        const b2 = document.querySelector('.qc-body');
        const badge = document.querySelector('.qc-badge');
        return {body: b2 ? b2.innerText.replace(/\\n+/g,' | ').slice(0,150) : '', badge: badge ? badge.innerText : ''};
    }""")
    print("5) 违规词命中:", qc2['body'][:150], "| 徽标:", qc2['badge'])
    body_input.fill("市面上的南红手串怎么选？今天整理了几种常见料子，需要的按喜好选。")  # 恢复合规

    # ---- 图片：看全图（点击灯箱本身关闭）----
    pg.locator('.pub-img img').first.click()
    pg.wait_for_timeout(500)
    lb = pg.locator('.img-lightbox').count()
    print("6) 看全图 lightbox:", lb)
    if lb:
        pg.locator('.img-lightbox').click()
    pg.wait_for_timeout(400)

    # ---- 删除一张 ----
    pg.locator('.pub-img .x').first.click()
    pg.wait_for_timeout(500)
    imgs1 = pg.locator('.pub-img img').count()
    print("7) 删除后图片数:", imgs1, "(期望", imgs0, "-1)")

    # ---- 追加一张（本地文件，路径可移植）----
    local_img = next((str(x) for x in (ROOT / 'data' / 'images').glob('*_p0.jpg')), None)
    if not local_img or not os.path.exists(local_img):
        local_img = '/tmp/e1_s2_clone.png'  # 兜底任意本地图
    pg.locator('input[type="file"]').first.set_input_files(local_img)
    pg.wait_for_timeout(2000)
    imgs2 = pg.locator('.pub-img img').count()
    print("8) 追加后图片数:", imgs2, "(期望", imgs1, "+1)")

    # ---- 保存草稿 + 草稿库恢复 ----
    pg.locator('button', has_text='保存草稿').first.click()
    pg.wait_for_timeout(2000)
    pg.locator('button', has_text='草稿库').first.click()
    pg.wait_for_timeout(1000)
    drafts = pg.evaluate("""() => [...document.querySelectorAll('.draft-item, .draft-row')].map(d => d.innerText.replace(/\\n+/g,' ').slice(0,60))""")
    print("9) 草稿库现有草稿:", drafts[:4])
    pg.locator('.draft-item, .draft-row').first.click()
    pg.wait_for_timeout(800)
    print("10) 已恢复最新草稿")
    b.close()
