#!/usr/bin/env python3
"""列 CDP 里所有页面/窗口及其几何，用于对照双窗形态（壳窗=APP / 采集窗=标准浏览器窗）。

用法（guest 内）：
  python list_windows.py
"""
import json
import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"


def main() -> int:
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        for ci, ctx in enumerate(browser.contexts):
            for pg in ctx.pages:
                row = {"ctx": ci, "url": pg.url, "title": None, "window": None, "bounds": None}
                try:
                    row["title"] = pg.title()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    sess = ctx.new_cdp_session(pg)
                    win = sess.send("Browser.getWindowForTarget")
                    row["window"] = win.get("windowId")
                    row["bounds"] = win.get("bounds")
                    try:
                        sess.send("Runtime.evaluate", {"expression": "1"})
                    finally:
                        sess.detach()
                except Exception as exc:  # noqa: BLE001
                    row["err"] = str(exc)[:100]
                rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
