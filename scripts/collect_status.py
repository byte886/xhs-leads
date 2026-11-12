#!/usr/bin/env python3
"""壳窗 + 采集窗一次快照：登录态、按钮状态、卡片数、状态栏文本。

用法：python collect_status.py
"""
import json

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"

JS = r"""
() => ({
  login_hint: !!document.querySelector('.login-hint, .login-tip'),
  btn_text: (document.querySelector('.btn-search') || {}).textContent || '',
  btn_stop: !!document.querySelector('.btn-search.stop'),
  card_count: document.querySelectorAll('.card').length,
  score_badges: document.querySelectorAll('.score-badge').length,
  keyword: (document.querySelector('input[placeholder*="关键词"], input') || {}).value || '',
  status_text: (document.querySelector('.info-bar') || {}).innerText || '',
  body_tail: (document.body.innerText || '').slice(-400),
})
"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        pages = [(q.url or "") for q in ctx.pages]
        shell = next((q for q in ctx.pages if "127.0.0.1" in (q.url or "")), None)
        collect = next((q for q in ctx.pages if "xiaohongshu.com" in (q.url or "")), None)
        out = {"pages": pages}
        if shell:
            out["shell"] = shell.evaluate(JS)
            out["shell_url"] = shell.url
            out["shell_cookies_ok"] = None
        if collect:
            out["collect_url"] = collect.url
        print(json.dumps(out, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
