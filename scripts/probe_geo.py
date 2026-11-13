# -*- coding: utf-8 -*-
"""Windows guest 侧探针：检查小红书搜索页的 geolocation 能力与筛选标签。"""
import json
import sys
from playwright.sync_api import sync_playwright

OUT = {}


def main():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pages = [pg for c in b.contexts for pg in c.pages]
        target = None
        for pg in pages:
            try:
                if "xiaohongshu.com/search_result" in pg.url:
                    target = pg
                    break
            except Exception:
                continue
        if target is None:
            OUT["error"] = "search_result page not found"
            print(json.dumps(OUT, ensure_ascii=True))
            return
        OUT["url"] = target.url

        try:
            OUT["perm"] = target.evaluate(
                "async () => (await navigator.permissions.query({name:'geolocation'})).state"
            )
        except Exception as e:
            OUT["perm_error"] = repr(e)

        try:
            OUT["geo"] = target.evaluate("""() => new Promise(r => {
                const t = setTimeout(() => r({ok:false, code:-9, msg:'no callback in 9s'}), 9000);
                navigator.geolocation.getCurrentPosition(
                  p => { clearTimeout(t); r({ok:true, lat:p.coords.latitude, lng:p.coords.longitude, acc:p.coords.accuracy}); },
                  e => { clearTimeout(t); r({ok:false, code:e.code, msg:e.message}); },
                  {timeout: 6000}
                );
            })""")
        except Exception as e:
            OUT["geo_error"] = repr(e)

        try:
            tags = target.evaluate(
                "() => [...document.querySelectorAll('div.tags')].map(x => ((x.innerText||'').trim())).slice(0, 80)"
            )
            OUT["tags_count"] = len(tags or [])
            OUT["has_tongcheng"] = any("同城" in (t or "") for t in (tags or []))
            OUT["tags_sample"] = (tags or [])[:40]
        except Exception as e:
            OUT["tags_error"] = repr(e)

        print(json.dumps(OUT, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"fatal": repr(e)}, ensure_ascii=True))
        sys.exit(1)
