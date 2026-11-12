#!/usr/bin/env python3
"""诊断壳窗为什么会出现两条竖向滚动条：量视口/页面/各容器的滚动状态。

用法：python inspect_shell_layout.py
"""
import json

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

JS = r"""
() => {
  const rows = [];
  const push = (label, el) => {
    if (!el) return;
    const cs = getComputedStyle(el);
    rows.push({
      label,
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 60),
      clientH: el.clientHeight,
      scrollH: el.scrollHeight,
      clientW: el.clientWidth,
      scrollW: el.scrollWidth,
      overflowY: cs.overflowY,
      overflowX: cs.overflowX,
      rect: (({x, y, width, height}) => ({x: Math.round(x), y: Math.round(y),
        w: Math.round(width), h: Math.round(height)}))(el.getBoundingClientRect()),
    });
  };
  rows.push({label: 'viewport', iw: innerWidth, ih: innerHeight,
             docScrollH: document.documentElement.scrollHeight,
             docClientH: document.documentElement.clientHeight,
             bodyScrollH: document.body.scrollHeight,
             bodyClientH: document.body.clientHeight});
  push('html', document.documentElement);
  push('body', document.body);
  const walk = (el, depth) => {
    if (depth > 5) return;
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      const scrolls = (c.scrollHeight > c.clientHeight + 2 && /auto|scroll/.test(cs.overflowY))
        || (c.scrollWidth > c.clientWidth + 2 && /auto|scroll/.test(cs.overflowX));
      if (scrolls) push('scroller', c);
      walk(c, depth + 1);
    }
  };
  walk(document.body, 0);
  return rows;
}
"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((q for q in ctx.pages if "127.0.0.1" in (q.url or "")), None)
        if page is None:
            print(json.dumps({"ok": False, "err": "no shell page"}))
            return 1
        data = page.evaluate(JS)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
