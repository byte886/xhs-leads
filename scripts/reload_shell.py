#!/usr/bin/env python3
"""重载壳窗页面，让新前端构建生效（不改窗口位置，仅 reload）。

用法：python reload_shell.py
"""
import json

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((q for q in ctx.pages if "127.0.0.1" in (q.url or "")), None)
        if page is None:
            print(json.dumps({"ok": False, "err": "no shell page"}))
            return 1
        page.reload()
        page.wait_for_timeout(2500)
        print(json.dumps({"ok": True, "url": page.url, "title": page.title()},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
