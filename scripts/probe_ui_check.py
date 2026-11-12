"""截图核对：粉丝未知在卡片角标与评分弹窗里是否真的可见。"""
import asyncio

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9223"
SHELL = "http://127.0.0.1:8791/"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if "127.0.0.1:8791" in (p.url or "")), None)
        if page is None:
            print("NO_SHELL_PAGE")
            return
        await page.goto(SHELL + "?stage=pick", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3.2)
        await page.screenshot(path="/tmp/ui_fans_cards.png")
        n_unknown = await page.locator(".score-unknown").count()
        n_cards = await page.locator(".score-badge").count()
        print(f"cards={n_cards} unknown_badges={n_unknown}")

        if n_unknown:
            await page.locator(".score-unknown").first.click()
            await asyncio.sleep(1.2)
            await page.screenshot(path="/tmp/ui_fans_modal.png")
            warn = await page.locator(".sm-warn").all_inner_texts()
            print("warn_text=", warn)
        print("DONE")
        await browser.close()


asyncio.run(main())
