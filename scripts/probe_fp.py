from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://127.0.0.1:9223", timeout=15000)
    page = None
    for ctx in b.contexts:
        for pg in ctx.pages:
            if "captcha" in (pg.url or ""):
                page = pg
    if page is None:
        print("NO_CAPTCHA")
        raise SystemExit(1)

    fp = page.evaluate(
        """() => {
            const out = {};
            out.webdriver = navigator.webdriver;
            out.ua = navigator.userAgent;
            out.plugins = navigator.plugins.length;
            out.languages = navigator.languages;
            out.chromeObj = typeof window.chrome;
            if (window.chrome) {
                out.chromeKeys = Object.keys(window.chrome);
                out.appIsInstalled = window.chrome.app ? window.chrome.app.isInstalled : 'n/a';
            }
            out.outerW = outerWidth; out.innerW = innerWidth;
            out.outerH = outerHeight; out.innerH = innerHeight;
            out.hasPermPrompt = 'Notification' in window
                ? Notification.permission : 'n/a';
            // puppeteer/playwright 常见破绽
            out.toStrHasNative = Function.prototype.toString.toString().includes('native code');
            out.docKeys = Object.keys(document).filter(k => k.startsWith('__')).slice(0, 10);
            out.winKeys = Object.keys(window).filter(k =>
                /pw|play|puppet|cdc|selenium|driver/i.test(k)).slice(0, 10);
            return out;
        }"""
    )
    for k, v in fp.items():
        print(k, "=", v)
