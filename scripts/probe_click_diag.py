"""诊断：按 uid 命中的作者链接，为什么点了不开新页（上层遮挡？不可点？）。"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension" / "lib" / "extract.js"
CDP = "http://127.0.0.1:9223"

CARD_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/search_result/"], a[href*="/explore/"]')) {
    const r = a.getBoundingClientRect();
    if (r.width >= 60 && r.height >= 60 && r.y > 100 && r.y < 1100)
      out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
  }
  return out.slice(0, 4);
}"""

DETAIL_JS = """(uid) => {
  const links = [...document.querySelectorAll('a[href*="/user/profile/"]')].map((a, i) => {
    const r = a.getBoundingClientRect();
    const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
    const top = document.elementFromPoint(cx, cy);
    return {i, href: (a.getAttribute('href')||'').slice(0,50),
            x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
            cx, cy, text: (a.innerText||'').trim().slice(0,20),
            match: (a.getAttribute('href')||'').includes('/user/profile/' + uid),
            topTag: top ? top.tagName : '', topCls: top ? (top.className||'').toString().slice(0,40) : '',
            topIsInside: top ? (a.contains(top) || top.contains(a)) : false};
  });
  return links;
}"""


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "search_result" in (p.url or "")), None)
        if page is None:
            print("NO_SEARCH_PAGE")
            return
        script = EXT.read_text(encoding="utf-8")
        cards = await page.evaluate(CARD_JS)
        print(f"[0] 卡片 {len(cards)}")
        await page.mouse.click(cards[0]["x"], cards[0]["y"])
        await asyncio.sleep(2.5)
        await page.evaluate(script)
        d = await page.evaluate("() => xhsExtractNoteDetail()")
        uid = d.get("user_id")
        print(f"[1] note={d.get('note_id')} uid={uid} nick={d.get('author_nick')!r}")

        links = await page.evaluate(DETAIL_JS, uid)
        print(f"[2] 共 {len(links)} 个用户链接，其中 uid 匹配 {sum(1 for l in links if l['match'])} 个")
        for l in links[:6]:
            print(f"     i={l['i']} match={l['match']} href={l['href'][:34]} "
                  f"({l['x']},{l['y']}) {l['w']}x{l['h']} text={l['text']!r}")
            print(f"       点击点({l['cx']},{l['cy']}) 上层={l['topTag']}.{l['topCls']} 属本链接={l['topIsInside']}")

        # 限定到的那个链接详情
        cands = [l for l in links if l["match"] and l["w"] >= 8 and l["h"] >= 8]
        print(f"[3] 可点候选 {len(cands)} 个")
        if not cands:
            await page.go_back()
            await browser.close()
            return
        target = cands[0]
        print(f"[4] 尝试点击 ({target['cx']},{target['cy']}) href={target['href']}")
        before = {id(p) for p in ctx.pages}
        await page.mouse.click(target["cx"], target["cy"])
        for sec in (1, 2, 3, 5):
            await asyncio.sleep(sec)
            fresh = [p for p in ctx.pages if id(p) not in before]
            print(f"    +{sec}s 新页={[(p.url or '')[:50] for p in fresh]} 共{len(ctx.pages)}页")

        for p in ctx.pages:
            if id(p) not in before and "/user/profile/" in (p.url or ""):
                await p.close()
        await page.go_back()
        await asyncio.sleep(1.2)
        print("DONE")
        await browser.close()


asyncio.run(main())
