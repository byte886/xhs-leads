import sys
from playwright.sync_api import sync_playwright

try:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223", timeout=15000)
        for ctx in b.contexts:
            for pg in ctx.pages:
                try:
                    title = pg.title()
                except Exception as exc:
                    title = f"<title failed: {exc}>"
                print(f"PAGE url={pg.url} title={title}")
                try:
                    info = pg.evaluate(
                        "() => ({w: innerWidth, h: innerHeight, sy: Math.round(scrollY),"
                        " cards: document.querySelectorAll('section.note-item').length,"
                        " tags: [...document.querySelectorAll('div.tags')].filter(e => Number(getComputedStyle(e).opacity) > 0.01).length})"
                    )
                    print(f"     info={info}")
                except Exception as exc:
                    print(f"     evaluate failed: {exc}")
except Exception as exc:
    print(f"CONNECT_FAILED {exc}")
    sys.exit(1)
print("PROBE_OK")
