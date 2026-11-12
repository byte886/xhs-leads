"""探测：详情页里「N 粉丝」这个数字归属谁；作者页 uid 能否从 URL 取到。"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension" / "lib" / "extract.js"
CDP = "http://127.0.0.1:9223"

FAN_OWNER_JS = """() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = (n.textContent || '').trim();
    if (!t.includes('粉丝')) continue;
    let el = n.parentElement;
    let cls = '', depth = 0, anc = [];
    while (el && depth < 5) {
      anc.push((el.className || '').toString().split(' ').slice(0,3).join('.'));
      el = el.parentElement; depth++;
    }
    const pe = n.parentElement;
    const r = pe.getBoundingClientRect();
    out.push({text: t.slice(0, 40), cls: (pe.className||'').toString().slice(0,60),
              x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
              anc: anc.slice(0,4)});
  }
  return out.slice(0, 10);
}"""

AUTHOR_LINK_JS = """() => {
  const ls = [...document.querySelectorAll('a[href*="/user/profile/"]')].map(a => {
    const r = a.getBoundingClientRect();
    return {href: a.getAttribute('href') || '', x: Math.round(r.x + r.width/2),
            y: Math.round(r.y + r.height/2), w: Math.round(r.width), h: Math.round(r.height),
            text: (a.innerText || '').trim().slice(0, 30)};
  });
  return ls.slice(0, 8);
}"""

CARD_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/search_result/"], a[href*="/explore/"]')) {
    const r = a.getBoundingClientRect();
    if (r.width >= 60 && r.height >= 60 && r.y > 100 && r.y < 1100)
      out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                href: a.getAttribute('href') || ''});
  }
  return out.slice(0, 6);
}"""


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
        cards = await page.evaluate(CARD_JS)
        print(f"[0] 卡片数={len(cards)}")
        if not cards:
            return
        await page.mouse.click(cards[0]["x"], cards[0]["y"])
        await asyncio.sleep(2.2)
        print(f"[1] 进详情 url={page.url[:100]}")
        await page.evaluate(script)

        owners = await page.evaluate(FAN_OWNER_JS)
        print(f"[2] 「粉丝」文本节点 {len(owners)} 处：")
        for o in owners:
            print(f"     text={o['text']!r} cls={o['cls']!r} pos=({o['x']},{o['y']}) anc={o['anc']}")

        links = await page.evaluate(AUTHOR_LINK_JS)
        print(f"[3] 作者链接前 8 个：")
        for l in links:
            print(f"     href={l['href'][:45]} pos=({l['x']},{l['y']}) {l['w']}x{l['h']} text={l['text']!r}")

        # 从详情页 URL 拿当前笔记 id，与卡片 href 对照
        print(f"[4] 卡片 href={cards[0]['href'][:70]}")

        # 点作者拿 URL 里的 uid
        pt = await page.evaluate("""() => {
          const band = [...document.querySelectorAll('a[href*="/user/profile/"]')].map(a => {
            const r = a.getBoundingClientRect();
            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                    w: Math.round(r.width), h: Math.round(r.height), href: a.getAttribute('href') || ''};
          }).filter(l => l.w >= 30 && l.h >= 10 && l.y > 40 && l.y < 400);
          return band.length ? band[0] : null;
        }""")
        if pt:
            before = {id(p) for p in ctx.pages}
            await page.mouse.click(pt["x"], pt["y"])
            fresh = None
            for _ in range(12):
                await asyncio.sleep(0.5)
                for p in ctx.pages:
                    if id(p) not in before and "/user/profile/" in (p.url or ""):
                        fresh = p
                        break
                if fresh:
                    break
            if fresh:
                await asyncio.sleep(1.0)
                await fresh.evaluate(script)
                u = await fresh.evaluate("() => xhsExtractUser()")
                print(f"[5] 作者页 URL={fresh.url}")
                print(f"[5] 页面 fans={u.get('fans')!r} userId字段={u.get('userId')!r} nick={u.get('nickname')!r}")
                await fresh.close()
            else:
                print("[5] 未新开作者页")
        await page.go_back()
        await asyncio.sleep(1.5)
        print("DONE")
        await browser.close()


asyncio.run(main())
