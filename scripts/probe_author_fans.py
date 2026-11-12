"""一次性探测：详情页点作者，为什么会拿不到粉丝数。

只读观察 + 点击，不改任何代码。
流程：进详情 -> 看 DOM 有无粉丝数 -> 点作者 -> 计时是否新开标签 -> 关标签 -> 返回列表 -> 下一条
"""
import asyncio
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension" / "lib" / "extract.js"
CDP = "http://127.0.0.1:9223"

DETAIL_STATS_JS = """() => {
  const txt = document.body.innerText || '';
  const hits = (txt.match(/([\\d.]+[万千wW]?)\\s*\\n?\\s*(粉丝|关注|获赞与收藏)/g) || []);
  return {
    hasFan: txt.includes('粉丝'),
    stats: hits.slice(0, 8),
    // 详情页里作者区通常带「粉丝」的统计；看是否在详情面板内就已存在
    panelStats: (() => {
      const p = document.querySelector('.note-detail, .note-container, [class*="detail"]');
      if (!p) return null;
      const t = (p.innerText || '');
      const m = (t.match(/([\\d.]+[万千wW]?)\\s*\\n?\\s*(粉丝|关注|获赞与收藏)/g) || []);
      return m.slice(0, 8);
    })(),
  };
}"""

CARD_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/search_result/"], a[href*="/explore/"]')) {
    const r = a.getBoundingClientRect();
    if (r.width >= 60 && r.height >= 60 && r.y > 100 && r.y < 1100)
      out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
  }
  return out.slice(0, 6);
}"""


async def probe_one(page, ctx, script, idx):
    before_ids = {id(p) for p in ctx.pages}
    before_urls = {p.url for p in ctx.pages}

    cards = await page.evaluate(CARD_JS)
    if not cards:
        print(f"[{idx}] 无可见卡片")
        return
    card = cards[0]
    await page.mouse.click(card["x"], card["y"])
    await asyncio.sleep(2.2)

    if "search_result_ai" in page.url:
        print(f"[{idx}] 未进详情 url={page.url[:60]}")
        return

    await page.evaluate(script)
    det = await page.evaluate(DETAIL_STATS_JS)
    links = await page.evaluate("""() => {
      const ls = [...document.querySelectorAll('a[href*="/user/profile/"]')].map(a => {
        const r = a.getBoundingClientRect();
        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                w: Math.round(r.width), h: Math.round(r.height), href: a.getAttribute('href') || ''};
      });
      const band = ls.filter(l => l.w >= 30 && l.h >= 10 && l.y > 40 && l.y < 400);
      return {total: ls.length, bandCount: band.length, bandY: band.map(l => l.y).slice(0,4),
              firstHref: ls.length ? ls[0].href.slice(0, 50) : ''};
    }""")
    print(f"[{idx}] 详情页 含粉丝={det['hasFan']} 全页统计串={det['stats']}")
    print(f"[{idx}]       详情面板内统计={det['panelStats']}")
    print(f"[{idx}] 作者链接 total={links['total']} 带内={links['bandCount']} 带内y={links['bandY']}")

    pt = await page.evaluate("""() => {
      const band = [...document.querySelectorAll('a[href*="/user/profile/"]')].map(a => {
        const r = a.getBoundingClientRect();
        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                w: Math.round(r.width), h: Math.round(r.height)};
      }).filter(l => l.w >= 30 && l.h >= 10 && l.y > 40 && l.y < 400);
      return band.length ? band[0] : null;
    }""")
    if not pt:
        print(f"[{idx}] 带内无作者链接 -> 当前代码会直接返回 {{}}")
    else:
        t0 = time.time()
        await page.mouse.click(pt["x"], pt["y"])
        found = None
        waited = 0.0
        for _ in range(30):
            await asyncio.sleep(0.5)
            waited = time.time() - t0
            for p in ctx.pages:
                if id(p) not in before_ids and "/user/profile/" in (p.url or "") and p.url not in before_urls:
                    found = p
                    break
            if found:
                break
        print(f"[{idx}] 点作者 y={pt['y']} -> 新开页={'YES' if found else 'NO'} 用时={waited:.1f}s")
        if found:
            await asyncio.sleep(1.0)
            await found.evaluate(script)
            u = await found.evaluate("() => xhsExtractUser()")
            print(f"[{idx}] 作者页 fans={u.get('fans')!r} userId={u.get('userId')!r} nick={u.get('nickname')!r}")
            href = links["firstHref"]
            print(f"[{idx}] 期望 uid(来自卡片href)~{href}")
            await found.close()

    # 回列表
    await page.go_back()
    await asyncio.sleep(1.5)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = None
        for p in ctx.pages:
            if "search_result" in (p.url or ""):
                page = p
                break
        if page is None:
            print("NO_SEARCH_PAGE")
            return

        script = EXT.read_text(encoding="utf-8")
        print(f"[0] 起始 url={page.url[:70]}")
        for i in range(1, 5):
            try:
                await probe_one(page, ctx, script, i)
            except Exception as e:  # noqa: BLE001
                print(f"[{i}] 异常 {type(e).__name__}: {e}")
                try:
                    await page.go_back()
                    await asyncio.sleep(1.5)
                except Exception:
                    pass
        print("DONE")


asyncio.run(main())
