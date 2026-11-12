#!/usr/bin/env python3
"""诊断：采集窗宽度与小红书搜索条可见性的关系。

逐个宽度用 CDP Browser.setWindowBounds 改真实窗口宽度，读搜索输入框 rect，
找出「搜索条不被折叠」的最小宽度，为 MIN_WIN_W / 采集窗最小宽度定值。

用法（guest 内）：
  %LOCALAPPDATA%\\xhs-leads-build\\.venv\\Scripts\\python.exe probe_search_breakpoint.py
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
WIDTHS = [640, 700, 760, 820, 880, 940, 1000, 1100, 1200]

MEASURE_JS = """() => {
  const cands = ['#search-input-in-feeds', 'textarea.search-input', '#search-input', 'input.search-input'];
  const rows = [];
  for (const sel of cands) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    rows.push({sel, w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y)});
  }
  // 折叠态的触发器（放大镜按钮）
  const trig = document.querySelector('a.search-icon, button.search-icon, .search-icon, #search-button');
  let trigRect = null;
  if (trig) {
    const r = trig.getBoundingClientRect();
    trigRect = {tag: trig.tagName, w: Math.round(r.width), h: Math.round(r.height),
                x: Math.round(r.x), y: Math.round(r.y)};
  }
  return {innerWidth: window.innerWidth, rows, trig: trigRect,
          areaDisplay: (() => {const a = document.querySelector('.search-area');
            return a ? getComputedStyle(a).display : null; })()};
}"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        target = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "xiaohongshu.com" in pg.url:
                    target = pg
        if target is None:
            print("no xiaohongshu page", file=sys.stderr)
            return 1
        sess = target.context.new_cdp_session(target)
        win = sess.send("Browser.getWindowForTarget")
        orig = win.get("bounds", {})
        out = {"original": orig, "samples": []}
        for w in WIDTHS:
            bounds = dict(orig)
            bounds.update({"width": w, "height": orig.get("height", 700)})
            sess.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": bounds})
            time.sleep(0.9)
            out["samples"].append({"asked_width": w, "m": target.evaluate(MEASURE_JS)})
        # 还原
        sess.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": orig})
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
