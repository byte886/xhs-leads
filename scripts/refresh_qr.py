#!/usr/bin/env python3
"""刷新小红书登录页二维码并截图（截图交给用户扫码用）。

用法：python refresh_qr.py <输出png>
"""
import json
import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

JS = """
() => {
  const cands = [...document.querySelectorAll('div,span,a,p')]
    .filter(el => el.children.length === 0 && (el.textContent || '').trim() === '点击刷新');
  if (!cands.length) return { clicked: false, reason: 'no refresh node' };
  const el = cands[cands.length - 1];
  el.click();
  return { clicked: true, tag: el.tagName, cls: el.className };
}
"""


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else r"\\Mac\Home\Desktop\qr.png"
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((q for q in ctx.pages if "xiaohongshu.com" in (q.url or "")), None)
        if page is None:
            print(json.dumps({"ok": False, "err": "no xhs page"}))
            return 1
        res = page.evaluate(JS)
        page.wait_for_timeout(1500)
        page.screenshot(path=out)
        print(json.dumps({"ok": True, "click": res, "url": page.url, "path": out},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
