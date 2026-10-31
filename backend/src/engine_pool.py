"""多账户引擎池（架构改造 P1）：Playwright launch_persistent_context 引擎管理。

替代 browser_client 的 Popen + connect_over_cdp 单例：
- 每账号一个独立 user_data_dir = 一个独立登录态（采集号/发布号分离的机制基础）
- launch_persistent_context(channel='chrome')：无授权弹窗、免钥匙串（PoC 五验证点全通过）
- 懒启动 / 优雅关闭（ctx.close() 落盘登录态，绝不用 pkill -9）/ 自愈重启
- 页面管理：后台建 tab + 激活、保底空白页、按账号清理无关 tab

账号注册表见 config.py（data/accounts.json）。风控状态见 risk.py。

用法（业务层 browser_client 使用）：
    from src import engine_pool
    ctx = await engine_pool.pool.get("default").context()
    page = await engine_pool.pool.get("collect_a").new_page()
"""
import asyncio
import os
import time

from playwright.async_api import async_playwright

from src import config, logger as xlog, risk

log = xlog.get("engine")

_pw = None
_pw_lock: asyncio.Lock | None = None

# 无头模式 UA（R 批 v1.4）：Chrome 无头 UA 带 HeadlessChrome 特征（风控指纹），
# 无头启动时覆盖为有头 UA。版本随本机 Chrome 漂移：低版本 UA 仅指纹差异不影响可用性，
# 升级 Chrome 时顺手更新版本号即可。
_HEADLESS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)

# 防自动化检测 stealth（小红书等风控会检测 navigator.webdriver / chrome 对象 /
# plugins / languages 等特征；注入后引擎与普通 Chrome 观感一致）。
# 保持轻量、无外部依赖；后续如风控升级可在不换机制前提下补充特征。
_STEALTH_SRC = r"""
// 直接语句形式（add_init_script 的字符串会被执行；箭头函数表达式只是创建不会执行）
window.__stealth_injected = true;
try { delete navigator.webdriver; } catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'webdriver', {
    get: () => undefined, configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(Navigator.prototype, 'plugins', {
    get: () => [1, 2, 3, 4, 5], configurable: true,
  });
} catch (e) {}
try {
  const originalQuery = Navigator.prototype.permissions
      && Navigator.prototype.permissions.query;
  if (originalQuery) {
    Navigator.prototype.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery.call(Navigator.prototype.permissions, parameters);
  }
} catch (e) {}
try {
  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {
    connect: () => {}, sendMessage: () => {}, id: undefined,
  };
} catch (e) {}
"""



async def _get_pw():
    """全局共享一个 Playwright 实例（多个 persistent context = 多个独立浏览器进程）。"""
    global _pw, _pw_lock
    if _pw_lock is None:
        _pw_lock = asyncio.Lock()
    async with _pw_lock:
        if _pw is None:
            _pw = await async_playwright().start()
        return _pw


class AccountEngine:
    """单账号引擎：持久化上下文 + 生命周期 + 页面管理。"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        acc = config.get_account(account_id) or config.add_account(account_id)
        self.user_data_dir = config.expand(acc.get("user_data_dir", ""))
        self.role = acc.get("role", "mixed")
        self._context = None
        self._lock = asyncio.Lock()
        self._fail_until = 0.0
        self._task_page = None  # 该账号唯一任务 tab（复用，减少反复开关 tab）

    # ---------------- 生命周期 ----------------

    async def context(self):
        """取（或懒启动）该账号的 persistent context。自愈：断开自动重建。"""
        async with self._lock:
            if self._context is not None:
                try:
                    if self._context.browser is not None and self._context.browser.is_connected():
                        return self._context
                except Exception:  # noqa: BLE001
                    pass
                # 连接已断开 → 释放旧引用，重建
                try:
                    await self._context.close()
                except Exception:  # noqa: BLE001
                    pass
                self._context = None
                log.warning("账号 %s 引擎连接断开，自动重建", self.account_id)

            now = time.monotonic()
            if self._fail_until > now:
                raise RuntimeError(
                    f"账号 {self.account_id} 引擎启动冷却中（{int(self._fail_until - now)}s 后再试）"
                )

            pw = await _get_pw()
            try:
                os.makedirs(self.user_data_dir, exist_ok=True)
                # 关键：launch_persistent_context（内部 stdio 管道，无 TCP 调试端口，
                # 从机制上不触发「允许远程调试」授权流程；解密在浏览器进程内部，免钥匙串）
                # R 批 v1.4：headless 无头化（外部零 Chrome 窗口）+ stealth 强化
                #  - viewport 1280x800：桌面宽屏布局（小红书桌面版），登录小窗/调试镜像同尺寸
                #  - user_agent：无头时覆盖 HeadlessChrome 特征（风控指纹）
                #  - AutomationControlled：关掉 CDP 来源的 webdriver 标记（与 init script 双保险）
                #  - headless=False 兜底档：风控敏感环节可临时切后台可见窗口（config.headless）
                cfg = config.get_config()
                headless = bool(cfg.get("headless", True))
                launch_kwargs = dict(
                    channel="chrome",
                    headless=headless,
                    viewport={"width": 1280, "height": 800},
                    args=[
                        "--no-first-run", "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                if headless:
                    launch_kwargs["user_agent"] = _HEADLESS_UA
                self._context = await pw.chromium.launch_persistent_context(
                    self.user_data_dir, **launch_kwargs
                )
                # stealth：隐藏自动化特征（webdriver 等），降低被风控识别概率
                try:
                    await self._context.add_init_script(_STEALTH_SRC)
                except Exception as se:  # noqa: BLE001
                    log.warning("stealth 注入失败（不影响引擎启动）: %s", se)
                xlog.audit(self.account_id, action="engine_start", ok=True,
                           detail=f"dir={config.home_relative(self.user_data_dir)}")
                return self._context
            except Exception as e:  # noqa: BLE001
                self._fail_until = time.monotonic() + 20
                xlog.audit(self.account_id, action="engine_start", ok=False,
                           detail=f"启动失败: {str(e)[:200]}")
                raise RuntimeError(f"账号 {self.account_id} 引擎启动失败: {e}") from e

    async def close(self):
        """优雅关闭：ctx.close() 落盘登录态（pkill -9 会丢登录态，已实证）。"""
        async with self._lock:
            if self._context is not None:
                try:
                    await self._context.close()
                    xlog.audit(self.account_id, action="engine_close", ok=True)
                except Exception as e:  # noqa: BLE001
                    log.warning("账号 %s 关闭异常: %s", self.account_id, e)
                self._context = None

    # ---------------- 页面管理 ----------------

    async def new_page(self):
        """取一个可用页面：优先复用该账号缓存的【任务 tab】（同 tab 导航覆盖，
        从机制上消除「反复开关 tab」的观感）；无缓存则后台新建 + 激活，
        失败退化为前台新页。新建后缓存为该账号任务 tab。

        后台 tab 会被 Chrome 渲染节流（SPA 不渲染 → 采集 0 条），建 tab 后立即
        Target.activateTarget 激活。
        """
        if self._task_page is not None:
            try:
                if not self._task_page.is_closed():
                    return self._task_page
            except Exception:  # noqa: BLE001
                pass
            self._task_page = None
        ctx = await self.context()
        try:
            src = ctx.pages[0]
            cdp = await ctx.new_cdp_session(src)
            resp = await cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
            try:
                await cdp.send("Target.activateTarget", {"targetId": resp["targetId"]})
            except Exception:  # noqa: BLE001
                pass
            for _ in range(50):
                for p in ctx.pages:
                    if p.url == "about:blank" and len(ctx.pages) > 1 and p != src:
                        self._task_page = p
                        return p
                await asyncio.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
        p = await ctx.new_page()
        self._task_page = p
        return p

    async def close_page_safe(self, page):
        """任务收尾：任务 tab【保留不关闭】（浏览器内留着，供下次任务复用导航 /
        登录墙页供用户扫码），仅兜底保证至少留一个空白页（防整窗退出反复开关）。

        引擎窗口本身常驻：不在任务级反复开-关 Chrome 进程（现象 1 机制核心）。
        真正清理无关 tab 走 cleanup_tabs()（如 APP 启动清理）。
        """
        try:
            ctx = page.context
            if len(ctx.pages) <= 1:
                await ctx.new_page()  # about:blank 保底
        except Exception:  # noqa: BLE001
            pass
        # 任务 tab 保留；若 page 不是任务 tab（如 _verify_profile 校验页），正常关闭
        if self._task_page is None or self._task_page != page:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass

    async def cleanup_tabs(self, keep_url: str = ""):
        """清理无关标签页：APP 前端页与 about:blank 保留，其余关闭。

        keep_url 缺省用 config.front_port() 拼 127.0.0.1:<port>（不再硬编码 8791）。
        """
        if not keep_url:
            keep_url = f"127.0.0.1:{config.front_port()}"
        try:
            ctx = await self.context()
            pages = ctx.pages
            closed = 0
            for p in pages:
                u = p.url or ""
                if keep_url in u or u == "about:blank":
                    continue  # APP 页/空白页保留
                try:
                    await p.close()
                    if self._task_page == p:
                        self._task_page = None
                    closed += 1
                except Exception:  # noqa: BLE001
                    continue
            if not ctx.pages:  # 全部清空 → 补空白页保底
                try:
                    await ctx.new_page()
                except Exception:  # noqa: BLE001
                    pass
            return closed
        except Exception as exc:  # noqa: BLE001
            log.warning("账号 %s cleanup_tabs 失败（非阻断）: %s", self.account_id, exc)
            return 0

    # ---------------- Cookie 辅助（按账号落盘） ----------------

    @property
    def cookie_file(self):
        """该账号的 Cookie 文件：data/cookies/<account_id>.json（default 兼容旧单份）。"""
        if self.account_id == "default":
            p = config.DATA_DIR / "cookies.json"
        else:
            p = config.DATA_DIR / "cookies" / f"{self.account_id}.json"
        return p

    async def read_cookies(self) -> list[dict]:
        """通过 CDP 读该账号引擎当前全部小红书 Cookie（只读，不关闭页面）。"""
        ctx = await self.context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        data = await cdp.send("Network.getAllCookies")
        return [
            c for c in data.get("cookies", [])
            if "xiaohongshu" in c.get("domain", "") or "xhscdn" in c.get("domain", "")
        ]

    async def inject_cookies(self, cookies: list[dict]) -> bool:
        """把 Cookie 注入该账号引擎（幂等，供校验/恢复用）。"""
        try:
            ctx = await self.context()
            await ctx.add_cookies(cookies)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("账号 %s 注入 Cookie 失败: %s", self.account_id, e)
            return False


class EnginePool:
    """账号 → AccountEngine 注册表（全局单例 pool）。"""

    def __init__(self):
        self._engines: dict[str, AccountEngine] = {}
        self._lock = asyncio.Lock()

    async def get(self, account_id: str = "default") -> AccountEngine:
        async with self._lock:
            e = self._engines.get(account_id)
            if e is None:
                e = AccountEngine(account_id)
                self._engines[account_id] = e
            return e

    def peek(self, account_id: str = "default") -> AccountEngine | None:
        """不启动：仅取已存在的引擎（登录态检测用，避免误拉起）。"""
        return self._engines.get(account_id)

    async def close(self, account_id: str = "default") -> bool:
        async with self._lock:
            e = self._engines.pop(account_id, None)
            if e is None:
                return False
            await e.close()
            return True

    async def close_all(self):
        """后端退出钩子：优雅关闭全部引擎 + Playwright 实例。"""
        async with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
        global _pw
        for e in engines:
            try:
                await e.close()
            except Exception:  # noqa: BLE001
                log.warning("close_all 账号 %s 关闭异常", e.account_id)
        if _pw is not None:
            try:
                await _pw.stop()
            except Exception:  # noqa: BLE001
                pass
            _pw = None
        log.info("引擎池已全部优雅关闭")


pool = EnginePool()
