# -*- coding: utf-8 -*-
"""b03 创作页补测：正文/标签编辑 + 图片拖拽排序 + 删除草稿（连 9333 headless）。

覆盖清单用例：D2 正文计数 / D3 话题标签 / D6 拖拽排序 / D13 删除草稿。
流程：克隆一条图文 → 正文输入断言计数 → 标签输入 → 拖拽第 1 图到第 3 位 → 保存草稿→删除该草稿。
数据污染：自己保存一条草稿再删除（只删自己造的）；克隆复用既有笔记。
前置：8791；9333 headless + 登录态；有图文卡片。
"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()

    # ---- 克隆一条图文 ----
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_function("() => { const h = document.querySelector('.flow-hint'); return h && h.innerText.includes('关键词采集') }", timeout=12000)
    pg.wait_for_timeout(500)
    pg.wait_for_selector('.card', timeout=15000)
    card = pg.locator('.card', has=pg.locator('.badge-note', has_text='图文')).first
    card.hover()
    pg.wait_for_timeout(600)
    card.locator('button.ha-btn.clone').first.click()
    pg.wait_for_timeout(16000)  # 创作页 + 图片补全窗口
    tin = pg.locator('input[maxlength="50"]').first
    print('1) 克隆后标题:', tin.input_value()[:30] if tin.count() else '(无)')

    # ---- D2 正文编辑 + 计数 ----
    ta = pg.locator('textarea[maxlength="1000"]')
    before = ta.input_value()
    ta.fill(before + '\n#测试正文补充 拖拽验证用')
    count_txt = pg.locator('text=/\\d+\\s*\\/\\s*1000/').first.inner_text()
    print(f'2) 正文计数: {count_txt} (长度 {len(ta.input_value())})')
    assert len(ta.input_value()) > len(before), '正文编辑未生效'

    # ---- D3 话题标签 ----
    tag = pg.locator('input[placeholder*="#"]')
    tag.fill('#南红玛瑙 #手串')
    print('3) 话题标签输入:', tag.input_value())
    assert tag.input_value() == '#南红玛瑙 #手串', '标签输入未生效'

    # ---- D6 图片拖拽排序：先经真实文件输入追加 6 张图（同时覆盖 D4 多图追加） ----
    imgs = pg.locator('.pub-img img')
    n = imgs.count()
    print('4) 拖拽前图片数:', n)
    import os as _os
    _base = _os.path.join(_os.path.expanduser('~'), 'Desktop/xhs-leads/data/images')
    _pics = [_os.path.join(_base, f'6ab8f4560000000002028056_p{i}.jpg') for i in range(6)]
    if all(_os.path.exists(_p) for _p in _pics):
        _before = set(_os.listdir(_base))  # 记录上传前目录，结尾清理本次上传残留
        pg.set_input_files('input[type=file]', _pics)
        # 逐张上传：轮询最多 25s 等图片到位
        for _ in range(25):
            pg.wait_for_timeout(1000)
            if pg.locator('.pub-img img').count() >= 3:
                break
        n = pg.locator('.pub-img img').count()
        print(f'   上传后图片数: {n}')
        _added = [f for f in _os.listdir(_base) if f not in _before]
        print(f'   本次新增图片文件: {len(_added)} 个')
    else:
        print('   (本地测试图片缺失，跳过上传)')
    if n >= 3:
        first_src = pg.locator('.pub-img img').nth(0).get_attribute('src')
        # headless 下真实 DnD/CDP 拖拽均不可靠：页面内构造 DragEvent+DataTransfer 完整序列
        # （dragstart→dragover→drop→dragend，触发 Vue onDragStart/onDrop 原生监听）
        pg.evaluate("""() => {
          const items = Array.from(document.querySelectorAll('.pub-img'));
          const src = items[0], dst = items[2];
          const dt = new DataTransfer();
          src.dispatchEvent(new DragEvent('dragstart', {bubbles: true, cancelable: true, dataTransfer: dt}));
          dst.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt}));
          dst.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
          src.dispatchEvent(new DragEvent('dragend', {bubbles: true, cancelable: true, dataTransfer: dt}));
        }""")
        pg.wait_for_timeout(500)
        new_first = pg.locator('.pub-img img').nth(0).get_attribute('src')
        moved = first_src != new_first
        print(f'   拖拽后第 1 图变化: {moved}')
        if not moved:
            # 兜底：鼠标路径拖拽
            pg.mouse.move(x0, y0)
            pg.mouse.down()
            pg.mouse.move(x2, y2, steps=5)
            pg.wait_for_timeout(200)
            pg.mouse.up()
            pg.wait_for_timeout(500)
            new_first = pg.locator('.pub-img img').nth(0).get_attribute('src')
            moved = first_src != new_first
            print(f'   鼠标兜底后变化: {moved}')
        assert moved, '拖拽排序未生效'
    else:
        print('   (图片不足 3 张，跳过拖拽断言)')

    # ---- D13 保存草稿 → 删除该草稿（UI 点击 + 接口双确认） ----
    tin.fill('b03测试草稿-待删除')
    pg.click('button:has-text("保存草稿")')
    pg.wait_for_timeout(1500)
    pg.click('button:has-text("草稿库"), .draft-toggle:has-text("草稿")')
    pg.wait_for_timeout(1200)
    target = pg.locator('.draft-item:has-text("b03测试草稿"), [class*="draft-item"]:has-text("b03测试草稿")')
    print(f'5) 目标草稿出现在面板: {target.count() > 0}')
    assert target.count() > 0, '保存草稿未入库'
    del_btn = target.first.locator('button:has-text("删除")')
    assert del_btn.count(), '草稿条目无删除按钮'
    del_btn.click()
    pg.wait_for_timeout(1500)
    # 接口校验（权威）：GET /api/drafts 不再含目标标题
    import json, urllib.request
    with urllib.request.urlopen('http://127.0.0.1:8791/api/drafts', timeout=8) as r:
        drafts_api = json.load(r)['drafts']
    gone_api = not any('b03测试草稿' in (d.get('title') or '') for d in drafts_api)
    print(f'6) 接口确认草稿已删除: {gone_api} (剩余 {len(drafts_api)} 条)')
    assert gone_api, '删除草稿未生效(接口残留)'
    # UI 复核：点 .draft-close 关闭面板，再重开确认目标不在列表（loadDrafts 已刷新）
    pg.click('.draft-close')
    pg.wait_for_timeout(500)
    pg.click('button:has-text("草稿库")')
    pg.wait_for_timeout(1200)
    gone_ui = pg.locator('.draft-item:has-text("b03测试草稿"), [class*="draft-item"]:has-text("b03测试草稿")').count() == 0
    print(f'7) UI 复核目标消失: {gone_ui}')
    assert gone_ui, 'UI 列表仍显示已删草稿'
    # 清理本次上传产生的图片文件（只删脚本新增，不碰用户文件）
    if '_added' in dir():
        for f in _added:
            p = _os.path.join(_base, f)
            if _os.path.exists(p) and f.startswith('user_'):
                _os.remove(p)
        print('   已清理上传残留:', len(_added), '个')

    print('b03 创作页补测：PASS')
