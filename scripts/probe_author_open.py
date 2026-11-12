"""验证两种绕开遮挡的取作者页方式：A) DOM 直接 click  B) 新标签页 goto 作者 URL。"""
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

DOMPICK_JS = """(uid) => {
  const links = [...document.querySelectorAll('a[href*="/user/profile/"]')]
    .filter(a => (a.getAttribute('href') || '').includes('/user/profile/' + uid));
  if (!links.length) return {ok: false, reason: 'no-link', href: ''};
  const a = links[0];
  const href = a.getAttribute('href') || '';
  a.click();
  return {ok: true, href, count: links.length};
}"""


async def probe(page, script, label):
    cards = await page.evaluate(CARD_JS)
    if not cards:
        print(f"{label}: 无卡片")
        return
    await page.mouse.click(cards[0]["x"], cards[0]["y"])
    await asyncio.sleep(2.5)
    await page.evaluate(script)
    d = await page.evaluate("() => xhsExtractNoteDetail()")
    uid = d.get("user_id")
    nick = d.get("author_nick")
    print(f"{label}: note={d.get('note_id')} 作者={nick!r} uid={uid}")
    return uid, nick


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "search_result" in (p.url or "")), None)
        if page is None:
            print("NO_SEARCH_PAGE")
            return
        script = EXT.read_text(encoding="utf-8")

        info = await probe(page, script, "[A]")
        if not info:
            return
        uid, nick = info

        # ---- 方式 A：DOM 直接 click ----
        res = await page.evaluate(DOMPICK_JS, uid)
        print(f"[A] a.click() -> {res}")
        before = set()
        for sec in (1, 2, 3, 5):
            await asyncio.sleep(sec)
            fresh = [p for p in ctx.pages if "/user/profile/" in (p.url or "") and id(p) not in before]
            print(f"    +{sec}s 作者页={[(p.url or '')[:60] for p in fresh]} 总{len(ctx.pages)}页")
            if fresh:
                for fp in fresh:
                    await asyncio.sleep(0.6)
                    await fp.evaluate(script)
                    u = await fp.evaluate("() => xhsExtractUser()")
                    got = (fp.url or "").split("/user/profile/")[-1][:24]
                    print(f"    取到 fans={u.get('fans')!r} nick={u.get('nickname')!r} "
                          f"页uid={got} 匹配={got == uid}")
                    await fp.close()
                break

        await page.go_back()
        await asyncio.sleep(1.5)

        # ---- 方式 B：新标签页 goto 作者 URL ----
        info = await probe(page, script, "[B]")
        if not info:
            return
        uid, nick = info
        href = await page.evaluate("""(uid) => {
          const a = [...document.querySelectorAll('a[href*="/user/profile/"]')]
            .find(x => (x.getAttribute('href') || '').includes('/user/profile/' + uid));
          return a ? a.getAttribute('href') : '';
        }""", uid)
        print(f"[B] 作者 href={href[:80]}")
        if href:
            url = href if href.startswith("http") else "https://www.xiaohongshu.com" + href
            np = await ctx.new_page()
            await np.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(1.2)
            await np.evaluate(script)
            u = await np.evaluate("() => xhsExtractUser()")
            got = (np.url or "").split("/user/profile/")[-1][:24]
            print(f"[B] 取到 fans={u.get('fans')!r} nick={u.get('nickname')!r} 页uid={got} 匹配={got == uid}")
            await np.close()

        await page.go_back()
        await asyncio.sleep(1.2)
        print("DONE")
        await browser.close()


asyncio.run(main())
