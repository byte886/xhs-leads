#!/usr/bin/env python3
"""把壳窗/采集窗摆成左壳 + 右采、等宽并排（Windows 1280×800 下的验收形态）。

用法：python arrange_windows.py [--shell-left 0] [--width 640] [--height 740]
输出：JSON（每窗摆位前后 bounds）
"""
import argparse
import json

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=740)
    ap.add_argument("--top", type=int, default=0)
    args = ap.parse_args()

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        for ctx in browser.contexts:
            for pg in ctx.pages:
                url = pg.url or ""
                role = "shell" if "127.0.0.1:8791" in url else "collect"
                left = 0 if role == "shell" else args.width
                sess = ctx.new_cdp_session(pg)
                try:
                    before = sess.send("Browser.getWindowForTarget")
                    sess.send("Browser.setWindowBounds", {
                        "windowId": before["windowId"],
                        "bounds": {"windowState": "normal"},
                    })
                    sess.send("Browser.setWindowBounds", {
                        "windowId": before["windowId"],
                        "bounds": {
                            "left": left, "top": args.top,
                            "width": args.width, "height": args.height,
                        },
                    })
                    after = sess.send("Browser.getWindowForTarget")
                    rows.append({"role": role, "url": url,
                                 "before": before.get("bounds"),
                                 "after": after.get("bounds")})
                finally:
                    sess.detach()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
