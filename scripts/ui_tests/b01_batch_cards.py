# -*- coding: utf-8 -*-
"""b01 卡片批量操作补测（连引擎调试端口 9223）。

覆盖清单用例：C5 全选图文 / C6 清空选择 / C7 单选 / C8 下载图片包 / C9 导出 JSON / C10 导出 CSV。
前置：8791 已启动（free 模式）；引擎 9223 已启动；notes 表有数据（采集页有卡片；无数据自动 SKIP=exit 2）。
"""
import sys
import urllib.request
import json as _json
from pathlib import Path

# 通过后端 API 判断是否有数据（数据目录可能由 XHS_LEADS_DATA 指向应用数据目录，
# 不硬编码仓库根数据库路径）
try:
    _total = _json.load(urllib.request.urlopen('http://127.0.0.1:8791/api/notes', timeout=10))['total']
except Exception as e:  # noqa: BLE001
    print('SKIP: 后端不可达（', str(e)[:60], '）')
    sys.exit(2)
if not _total:
    print('SKIP: 无笔记数据（notes 为空，需先采集；不进入 UI 交互）')
    sys.exit(2)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = b.contexts[0]
    pg = next((x for x in ctx.pages if '8791' in (x.url or '')), None)
    if pg is None:
        pg = ctx.new_page()
    pg.goto('http://127.0.0.1:8791/?stage=pick', timeout=20000)
    pg.wait_for_selector('.card', timeout=15000)
    pg.wait_for_timeout(500)

    # 批量区存在
    batch_visible = pg.locator('button.b-btn', has_text='全选图文').count() > 0
    print('1) 批量区存在:', batch_visible)

    # C5 全选图文（语义=只选图文类型卡片）
    pg.click('button.b-btn:has-text("全选图文")')
    pg.wait_for_timeout(300)
    total = pg.locator('.card').count()
    sel = pg.locator('.card.sel').count()
    img_cards = pg.locator('.card:has(.badge-note:text("图文"))').count()
    vid_sel = pg.locator('.card.sel:has(.badge-note:text("视频"))').count()
    print(f'2) 全选后选中数: {sel} (图文卡 {img_cards} / 总卡 {total})')
    # 语义：选中数恰为图文卡数，且任何视频卡都不被选中（全视频时选中 0 也正确）
    assert sel == img_cards and vid_sel == 0, '全选图文未生效'

    # C6 清空选择
    pg.click('button.b-btn:has-text("清空选择")')
    pg.wait_for_timeout(300)
    sel0 = pg.locator('.card.sel').count()
    print('3) 清空后选中数:', sel0)
    assert sel0 == 0, '清空未生效'

    # C7 单选（点第一张卡）
    pg.locator('.card').first.click()
    pg.wait_for_timeout(300)
    sel1 = pg.locator('.card.sel').count()
    print('4) 单选后选中数:', sel1)
    assert sel1 == 1, '单选未生效'

    # 注入 mock 另存为：系统原生保存对话框无法自动化，这里替换 showSaveFilePicker，
    # 把 Blob 内容经 expose_function 桥接写到 /tmp/ui_saves，完整验证前端另存为链路。
    import base64
    save_dir = Path('/tmp/ui_saves')
    save_dir.mkdir(parents=True, exist_ok=True)

    def _ui_save_blob(args):
        (save_dir / args['name']).write_bytes(base64.b64decode(args['b64']))
        return args['name']

    pg.expose_function('__ui_save_blob', _ui_save_blob)
    pg.evaluate('''() => {
      window.__saves = [];
      window.showSaveFilePicker = async ({ suggestedName }) => ({
        createWritable: () => {
          let blob = null;
          return {
            write: async (b) => { blob = b; },
            close: async () => {
              const bytes = new Uint8Array(await blob.arrayBuffer());
              let bin = '';
              for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
              await window.__ui_save_blob({ name: suggestedName, b64: btoa(bin) });
              window.__saves.push(suggestedName);
            }
          };
        }
      });
    }''')

    # C8 下载图片包（zip 经 mock 另存为落盘）
    pg.click('button.b-btn:has-text("下载图片包")')
    pg.wait_for_function("window.__saves.length >= 1", timeout=15000)
    zip_name = pg.evaluate("window.__saves[window.__saves.length - 1]")
    zip_bytes = (save_dir / zip_name).read_bytes()
    print(f'5) 图片包另存为: {zip_name} size={len(zip_bytes)}')
    assert zip_bytes.startswith(b'PK') and 'xhs_images' in zip_name, '图片包产物异常'

    # C9 导出 JSON
    pg.click('button.b-btn:has-text("导出 JSON")')
    pg.wait_for_function("window.__saves.length >= 2", timeout=15000)
    json_name = pg.evaluate("window.__saves[window.__saves.length - 1]")
    import json
    data = json.loads((save_dir / json_name).read_text(encoding='utf-8'))
    print(f'6) JSON 导出: {len(data)} 条')
    assert len(data) >= 1 and 'feed_id' in data[0], 'JSON 内容异常'

    # C10 导出 CSV
    pg.click('button.b-btn:has-text("导出 CSV")')
    pg.wait_for_function("window.__saves.length >= 3", timeout=15000)
    csv_name = pg.evaluate("window.__saves[window.__saves.length - 1]")
    lines = (save_dir / csv_name).read_text(encoding='utf-8-sig').strip().split('\n')
    print(f'7) CSV 导出: 表头+{len(lines)-1} 行')
    assert len(lines) >= 2 and '标题' in lines[0], 'CSV 内容异常'

    print('b01 批量操作：PASS')
