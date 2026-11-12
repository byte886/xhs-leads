#!/usr/bin/env python3
"""诊断：Windows 采集窗里搜索框聚焦失败的真因。

连 CDP（默认 9223）读小红书页面状态：视口尺寸、候选搜索框的可见性/rect、
focus() 之后 activeElement 到底是什么。输出 JSON 到 stdout。

用法（guest 内）：
  %LOCALAPPDATA%\\xhs-leads-build\\.venv\\Scripts\\python.exe probe_search_box.py
"""
import json
import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

PROBE_JS = """() => {
  const cands = ['#search-input-in-feeds', 'textarea.search-input', '#search-input',
                 'input.search-input', '.search-input', '#search-input-in-header',
                 'header textarea', 'header input'];
  const out = {
    href: location.href,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    dpr: window.devicePixelRatio,
    activeBefore: document.activeElement ? document.activeElement.tagName + '.' + (document.activeElement.className || '') : null,
    bodyText: (document.body ? document.body.innerText.slice(0, 80) : ''),
    candidates: {},
  };
  for (const sel of cands) {
    const list = [...document.querySelectorAll(sel)];
    out.candidates[sel] = list.map(el => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        tag: el.tagName,
        id: el.id || '',
        cls: (el.className || '').toString().slice(0, 60),
        x: Math.round(r.x), y: Math.round(r.y),
        w: Math.round(r.width), h: Math.round(r.height),
        display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
        placeholder: el.placeholder || '',
        readonly: !!el.readOnly,
        disabled: !!el.disabled,
      };
    });
  }
  // 真实点击位置命中什么
  const first = document.querySelector('#search-input-in-feeds') ||
                document.querySelector('textarea.search-input');
  if (first) {
    const r = first.getBoundingClientRect();
    const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
    const hit = document.elementFromPoint(cx, cy);
    out.hitAtCenter = hit ? hit.tagName + '.' + (hit.className || '').toString().slice(0, 40) : null;
    first.focus();
    out.activeAfterFocus = document.activeElement ? document.activeElement.tagName + '#' +
      (document.activeElement.id || '') + '.' + (document.activeElement.className || '').toString().slice(0, 40) : null;
    out.focusStuck = document.activeElement === first;
    // 判断 el 是否被祖先隐藏（visibility/display/尺寸/父级 clip）
    let hid = null, p = first;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
        hid = p.tagName + '.' + (p.className || '').toString().slice(0, 40) + ' -> ' + cs.display + '/' + cs.visibility + '/' + cs.opacity;
        break;
      }
      p = p.parentElement;
    }
    out.hiddenBy = hid;
  }
  return out;
}"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        infos = []
        for ctx in browser.contexts:
            for pg in ctx.pages:
                try:
                    url = pg.url
                except Exception:  # noqa: BLE001
                    url = "?"
                info = {"url": url}
                if "xiaohongshu.com" in url:
                    try:
                        info["probe"] = pg.evaluate(PROBE_JS)
                    except Exception as e:  # noqa: BLE001
                        info["probe_error"] = repr(e)
                infos.append(info)
        json.dump(infos, sys.stdout, ensure_ascii=False, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
