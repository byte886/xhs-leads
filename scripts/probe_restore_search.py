"""把被探测导航走的页面退回搜索页；顺手关掉多开的作者页。"""
import asyncio
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        for p in ctx.pages:
            if "/user/profile/" in (p.url or ""):
                print(f"关闭作者页 {p.url[:60]}")
                await p.close()
        for p in ctx.pages:
            u = p.url or ""
            if "127.0.0.1" in u:
                continue
            if "search_result_ai" in u:
                print(f"已是搜索页 {u[:60]}")
                continue
            if "xiaohongshu.com" in u:
                for _ in range(3):
                    await p.go_back()
                    await asyncio.sleep(1.2)
                    if "search_result_ai" in (p.url or ""):
                        break
                print(f"后退后 url={(p.url or '')[:70]}")
        for p in ctx.pages:
            print(f"现存 {(p.url or '')[:70]}")
        await browser.close()


asyncio.run(main())
