#!/usr/bin/env python3
"""验证修复：窄窗口（640）下点开放大镜 → 搜索浮层出现 → 聚焦输入 → 回车 → 进结果页。

同时打印「折叠态放大镜」到底是哪个元素（供 _EXPAND_SEARCH_JS 的选择器定值）。

用法（guest 内）：
  %LOCALAPPDATA%\\xhs-leads-build\\.venv\\Scripts\\python.exe probe_search_expand.py [关键词]
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
KW = sys.argv[1] if len(sys.argv) > 1 else "翡翠手串"

AT_POINT_JS = """() => {
  const el = document.elementFromPoint(296, 36) || document.elementFromPoint(276, 16);
  if (!el) return null;
  const chain = [];
  let p = el;
  for (let i = 0; i < 4 && p; i++) {
    chain.push(p.tagName + '#' + (p.id || '') + '.' + (p.className || '').toString().slice(0, 50));
    p = p.parentElement;
  }
  return chain;
}"""

VISIBLE_INPUTS_JS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('input, textarea')) {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    out.push({tag: el.tagName, id: el.id || '', cls: (el.className || '').toString().slice(0, 40),
              x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
              ph: el.placeholder || ''});
  }
  return out;
}"""

CARDS_JS = """() => {
  const sels = ['section.note-item', '.note-item', 'a.cover', '.feeds-page .note-item'];
  for (const s of sels) {
    const n = document.querySelectorAll(s).length;
    if (n) return {sel: s, n};
  }
  return {sel: null, n: 0};
}"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "xiaohongshu.com" in pg.url:
                    page = pg
        if page is None:
            print("no page", file=sys.stderr)
            return 1
        rep = {"start_url": page.url, "start_inputs": page.evaluate(VISIBLE_INPUTS_JS)}
        rep["hit_chain"] = page.evaluate(AT_POINT_JS)

        # 1) 模拟 _EXPAND_SEARCH_JS：按候选选择器找 40×40 的图标并点击
        clicked = page.evaluate("""() => {
          const cands = ['#search-button', 'a.search-icon', 'button.search-icon', '.search-icon',
                         'header .search-icon', 'div.search-icon', '.search-area .icon'];
          for (const sel of cands) {
            for (const el of document.querySelectorAll(sel)) {
              const r = el.getBoundingClientRect();
              if (r.width < 8 || r.height < 8) continue;
              el.click();
              return {sel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)};
            }
          }
          return null;
        }""")
        rep["expand_click"] = clicked
        time.sleep(1.3)
        rep["inputs_after_expand"] = page.evaluate(VISIBLE_INPUTS_JS)

        # 2) 聚焦可见输入框（与 _FOCUS_SEARCH_BOX_JS 同逻辑）
        focused = page.evaluate("""() => {
          const cands = ['#search-input-ai', '#search-input-in-feeds', 'textarea.search-input',
                         '#search-input', 'input.search-input', 'textarea.textarea'];
          for (const sel of cands) {
            for (const el of document.querySelectorAll(sel)) {
              const r = el.getBoundingClientRect();
              if (r.width < 20 || r.height < 10) continue;
              el.focus();
              if (document.activeElement === el || el.contains(document.activeElement)) return true;
            }
          }
          for (const el of document.querySelectorAll('input, textarea')) {
            const r = el.getBoundingClientRect();
            if (r.width < 60 || r.height < 10) continue;
            if (el.readOnly || el.disabled) continue;
            el.focus();
            if (document.activeElement === el || el.contains(document.activeElement)) return true;
          }
          return false;
        }""")
        rep["focused"] = focused

        # 3) 输入 + 回车
        if focused:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(KW, delay=90)
            time.sleep(0.5)
            page.keyboard.press("Enter")
            deadline = time.time() + 12
            while time.time() < deadline and "search_result" not in page.url:
                time.sleep(0.3)
            rep["url_after_enter"] = page.url
            time.sleep(1.5)
            rep["cards"] = page.evaluate(CARDS_JS)
            rep["url_final"] = page.url
        json.dump(rep, sys.stdout, ensure_ascii=False, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
