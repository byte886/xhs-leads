#!/usr/bin/env python3
"""抓采集窗（小红书页）的页面截图 + 关键几何，用于核对登录页在 640 宽窗内是否完整。

用法：python shot_collect.py <输出png路径>
"""
import json
import sys

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else r"\\Mac\Home\Desktop\collect_shot.png"
    info = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        target = None
        for pg in ctx.pages:
            if "xiaohongshu.com" in (pg.url or ""):
                target = pg
                break
        if target is None:
            print(json.dumps({"ok": False, "err": "no xiaohongshu page"}, ensure_ascii=False))
            return 1
        vp = target.evaluate(
            "() => ({iw: innerWidth, ih: innerHeight,"
            " dpr: devicePixelRatio, scrollW: document.body.scrollWidth})"
        )
        info.update({"url": target.url, "viewport": vp})
        target.screenshot(path=out, full_page=False)
        info["ok"] = True
        info["path"] = out
        print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
