# -*- coding: utf-8 -*-
"""Windows guest 侧探针 2：复现「展开筛选面板后能否找到并点到『同城』」。

只读探测：展开面板 -> 列出可见标签 -> 找同城（data-hp-kind 与文本两条路径）
-> 取点击坐标 -> 收起面板。不实际点击任何筛选项。
"""
import json
import time
from playwright.sync_api import sync_playwright

OPEN_JS = """() => {
    const vis = [...document.querySelectorAll('div.tags')]
        .filter(e => Number(getComputedStyle(e).opacity) > 0.01).length;
    if (vis > 5) return {open: true};
    const el = document.querySelector('div.filter')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('筛选');
        });
    if (!el) return null;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {open: false, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""

OPEN_CLICK_JS = """() => {
    const el = document.querySelector('div.filter')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('筛选');
        });
    if (!el) return false;
    el.click();
    return true;
}"""

VIS_JS = """() => [...document.querySelectorAll('div.tags')]
    .filter(e => Number(getComputedStyle(e).opacity) > 0.01).length"""

LIST_JS = """() => [...document.querySelectorAll('div.tags')].map(e => {
    const r = e.getBoundingClientRect();
    return {
        t: ((e.innerText || '').trim().split('\\n')[0] || ''),
        cls: e.className,
        op: Number(getComputedStyle(e).opacity),
        w: Math.round(r.width),
        h: Math.round(r.height),
        x: Math.round(r.x + r.width / 2),
        y: Math.round(r.y + r.height / 2)
    };
})"""

POINT_JS = """(val) => {
  const want = 'xhs-filter-tag-' + val;
  let el = [...document.querySelectorAll('[data-hp-kind]')]
    .find(e => e.getAttribute('data-hp-kind') === want);
  let via = 'hp-kind';
  if (!el) {
    via = 'text';
    el = [...document.querySelectorAll('div.tags')].find(x => {
      const t = ((x.innerText || '').trim().split(/\\s+/)[0] || '');
      return t === val && Number(getComputedStyle(x).opacity) > 0.01;
    });
  }
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {via, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
          text: (el.innerText || '').trim()};
}"""


def main():
    out = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = None
        for ctx in b.contexts:
            for pg in ctx.pages:
                try:
                    if "xiaohongshu.com/search_result" in pg.url:
                        page = pg
                        break
                except Exception:
                    continue
        if page is None:
            print(json.dumps({"error": "page not found"}))
            return
        out["viewport"] = page.evaluate("() => ({w: innerWidth, h: innerHeight})")
        out["tags_before"] = page.evaluate(VIS_JS)

        opened = page.evaluate(OPEN_JS)
        out["open_result"] = opened
        # 与生产代码一致：走 JS click（该页面对程序化鼠标事件不响应）
        out["js_click"] = page.evaluate(OPEN_CLICK_JS)

        vis = 0
        for _ in range(12):
            vis = page.evaluate(VIS_JS)
            if isinstance(vis, int) and vis >= 5:
                break
            time.sleep(0.4)
        out["vis_after_open"] = vis
        out["tags_after_open"] = page.evaluate(LIST_JS)
        out["point_tongcheng"] = page.evaluate(POINT_JS, "同城")
        out["point_buxian"] = page.evaluate(POINT_JS, "不限")
        print(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main()
