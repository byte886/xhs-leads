"""真机验证：修好的取粉丝逻辑，取回的是不是「当前笔记作者」的粉丝数。

不走后端进程（避免 engine_pool 抢锁），用外部 CDP 直连已运行的 CFT，
直接调 browser_client 里改过的那套函数，和旧逻辑的结果做对照。
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from src import browser_client, injections  # noqa: E402

CDP = "http://127.0.0.1:9223"

CARD_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/search_result/"], a[href*="/explore/"]')) {
    const r = a.getBoundingClientRect();
    if (r.width >= 60 && r.height >= 60 && r.y > 100 && r.y < 1100)
      out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                href: a.getAttribute('href') || ''});
  }
  return out.slice(0, 8);
}"""

OLD_JS = """() => {
  const links = [...document.querySelectorAll('a[href*="/user/profile/"]')].map(a => {
    const r = a.getBoundingClientRect();
    return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
             w: Math.round(r.width), h: Math.round(r.height),
             href: a.getAttribute('href') || '' };
  }).filter(l => l.w >= 30 && l.h >= 10 && l.y > 40 && l.y < 400);
  return links.length ? links[0] : null;
}"""


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "search_result" in (p.url or "")), None)
        if page is None:
            print("NO_SEARCH_PAGE")
            return

        cards = await page.evaluate(CARD_JS)
        print(f"[0] 可点卡片 {len(cards)} 张")
        await page.mouse.click(cards[0]["x"], cards[0]["y"])
        await asyncio.sleep(2.2)
        await injections.inject_once(page, "extract")
        d = await page.evaluate("() => xhsExtractNoteDetail()")
        uid = d.get("user_id")
        title = (d.get("title") or "")[:24]
        print(f"[1] 当前笔记 标题={title!r} 作者uid={uid}")

        old_pt = await page.evaluate(OLD_JS)
        print(f"[2] 旧逻辑会点 pos={None if not old_pt else (old_pt['x'], old_pt['y'])}"
              f" href={None if not old_pt else old_pt['href'][:45]}")

        new_pt = await page.evaluate(browser_client._AUTHOR_POINT_JS, uid)
        print(f"[3] 新逻辑命中 pos={None if not new_pt else (new_pt['x'], new_pt['y'])}"
              f" href={None if not new_pt else new_pt['href'][:45]}")

        fans = await browser_client._read_author_fans(page, uid, "default")
        print(f"[4] 新链路取回 fans={fans!r}")

        await page.go_back()
        await asyncio.sleep(1.5)
        print("DONE")
        await browser.close()


asyncio.run(main())
