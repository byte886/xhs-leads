#!/usr/bin/env python3
"""用 CDP Target.getTargets 列出全部标签（含 Playwright 未 attach 的），带所属窗口号。

用法：python list_targets.py
"""
import json

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        pg = ctx.pages[0]
        sess = ctx.new_cdp_session(pg)
        raw = sess.send("Target.getTargets")
        rows = []
        for t in raw["targetInfos"]:
            row = {"type": t["type"], "targetId": t["targetId"][:8], "url": t["url"], "title": t["title"],
                   "attached": t.get("attached"), "window": None}
            try:
                w = sess.send("Browser.getWindowForTarget", {"targetId": t["targetId"]})
                row["window"] = w.get("windowId")
                row["bounds"] = w.get("bounds")
            except Exception as exc:  # noqa: BLE001
                row["err"] = str(exc)[:80]
            rows.append(row)
        sess.detach()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
