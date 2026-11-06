"""Playwright 浏览器业务层（架构改造 P1 后）—— 引擎获取统一走 engine_pool。

设计：
- 引擎（多账户引擎池）见 engine_pool.py：每账号独立 user_data_dir / 独立登录态，
  launch_persistent_context(channel='chrome') 无授权弹窗、免钥匙串（PoC 已验证）。
- 本模块只保留【业务逻辑】：搜索/筛选/登录墙检测/详情/作者/评论/登录判定/Cookie 校验，
  全部通过 engine_pool.pool.get(account_id) 取上下文；函数签名新增可选 account_id
  （默认 "default"，向后兼容现有调用方）。
- DOM 提取复用 extension/lib/extract.js（页面内 evaluate 注入，单一提取源）。
- headless 登录判定（无窗口）保留独立逻辑，不占用账号引擎。
"""
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from src import config, engine_pool, injections, logger as xlog, risk
from src.cdp_timeouts import (CdpTimeoutError, CDP_DRAIN, CDP_EVAL, CDP_NAV,
                              CDP_PREP, CDP_QUICK, cdp_call)
from src.engine_pool import pool
from src.urls import build_url

log = xlog.get("browser")

if getattr(sys, "_MEIPASS", None):
    # 打包态（PyInstaller macOS）：资源随包进 Contents/Resources
    _ROOT = pathlib.Path(sys._MEIPASS).parent / "Resources"
else:
    _ROOT = pathlib.Path(__file__).resolve().parents[2]  # backend/src -> 项目根
# 提取脚本统一由 injections 注册表提供（R-6.30a）：extract 来源 = extension/lib/extract.js
_XHS = "https://www.xiaohongshu.com"


class BrowserLoginRequiredError(Exception):
    """搜索页被登录墙 / 风控验证拦截：Cookie 在但页面要求登录或验证（无法正常采集）。

    采集链路捕获后应中止任务并给出明确提示（会话失效 或 触发风控需人工验证），
    绝不继续空转滚动。"""
    pass


async def _ctx(account_id: str = "default"):
    """取账号引擎的 persistent context（懒启动）。"""
    return await (await pool.get(account_id)).context()


async def _engine(account_id: str = "default"):
    """取账号引擎实例。"""
    return await pool.get(account_id)


# ===========================================================================
# 页面辅助（委托 engine_pool）
# ===========================================================================
async def _page(account_id: str = "default"):
    """取一个可用页面（后台新建 + 激活；失败退化为前台新页）。"""
    return await (await _engine(account_id)).new_page()


async def _close_page_safe(page, account_id: str = "default"):
    """关闭页面但保证浏览器至少留一个空白页（防整窗退出反复开关）。"""
    await (await _engine(account_id)).close_page_safe(page)


async def cleanup_tabs(account_id: str = "default") -> int:
    """清理账号引擎中与任务无关的标签页：APP 前端页（动态端口）与 about:blank 保留。"""
    return await (await _engine(account_id)).cleanup_tabs()


def _is_crash_error(e: BaseException) -> bool:
    """R-6.53：判定 Playwright 页面/渲染崩溃类异常（Page crashed / Target closed）。

    真实采集取证（Nov 5）：CFT 156 在 macOS 上导航/运行小红书 search_result 页会
    概率性渲染进程崩溃（Playwright 报 Page crashed / Target page ... has been closed），
    轻则采集失败、重则级联主进程 SIGTRAP（17 份 .ips，Chrome_IOThread CHECK）。
    采集链路据此自动重开页面重试，把「崩溃即失败」变为「崩溃自动恢复继续采集」。
    """
    if isinstance(e, asyncio.TimeoutError):
        return False
    msg = str(e)
    return (
        "crashed" in msg
        or "has been closed" in msg
        or "Target page, context or browser has been closed" in msg
        or "Page crashed" in msg
    )


async def recover_collect_window(account_id: str = "default") -> int:
    """R-6.53v：停止后恢复采集窗口——清理残留采集页（搜索/笔记/探索页），
    引擎不再有采集页在导航/滚动（CDP 行为完全停止）。登录墙页保留。"""
    return await _cleanup_stale_xhs_pages(account_id)


def _is_purgeable_xhs_page(url: str) -> bool:
    """R-6.53：是否为可清理的旧 xhs 页（登录墙 / 验证码页保留）。"""
    return (
        "xiaohongshu.com" in url
        and "/website-login" not in url
        and "captcha" not in url
    )


async def _close_purgeable_pages(pages) -> int:
    """逐页关闭可清理页并计数（关闭失败静默跳过）。"""
    closed = 0
    for p in pages:
        if not _is_purgeable_xhs_page(p.url or ""):
            continue
        try:
            await p.close()
            closed += 1
        except Exception:  # noqa: BLE001
            pass
    return closed


async def _cleanup_stale_xhs_pages(account_id: str = "default") -> int:
    """R-6.53：采集前清理残留的小红书搜索/探索页（登录墙页保留）。

    取证（Nov 5）：历史采集崩溃/中断后残留 search_result 页会僵死（CDP WS 无响应）
    且 cleanup_tabs 按设计保留所有 xiaohongshu.com tab → 页面累积放大引擎负担，
    与崩溃相互促进。每次采集前清掉非登录墙的旧 xhs 页，降低累积触发面。
    """
    try:
        eng = await _engine(account_id)
        ctx = await eng.context()
        closed = await _close_purgeable_pages(list(ctx.pages))
        if closed:
            log.info("采集前清理残留 xhs 页 %d 个", closed)
        return closed
    except Exception:  # noqa: BLE001
        return 0

# ===========================================================================
# 页面加载与提取
# ===========================================================================
async def _load_focus(page, show_front: bool, tag: str = "load") -> None:
    """焦点处理（R-6.18）：前台可见 or 后台切回壳窗口 Tab1。失败非阻断。

    R-6.53q：CFT 158 在后台 tab 上 Page.bringToFront 会永久挂起（CDP 假死直接
    触发点），焦点切换非关键路径，故统一走 cdp_call 绝对超时 + skip。
    """
    try:
        if show_front:
            await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                           label=f"{tag}.bring_to_front", skip=True)
        else:
            await _switch_back_to_shell(page)
    except Exception:  # noqa: BLE001
        pass


async def _load_open(page, url: str, wait_ms: int, show_front: bool,
                     suffix: str = ""):
    """单次尝试：导航 + 焦点 + 注入提取脚本 + 等待渲染。

    R-6.53s：统一 CDP 超时保护——引擎假死时 Playwright 内部 timeout 不触发
    （CDP 无响应），cdp_call 以绝对超时强制打断并打 WARNING 溯源。
    suffix 用于区分首次与重试的日志标签（load.goto / load.goto_retry）。
    """
    await cdp_call(page.goto(url, wait_until="domcontentloaded", timeout=12000),
                   timeout=CDP_NAV, label=f"load.goto{suffix}")
    await _load_focus(page, show_front, tag=f"load{suffix}")
    await injections.inject_once(page, "extract")
    await asyncio.sleep(wait_ms / 1000)
    return page


async def _load_retry(page, url: str, wait_ms: int, show_front: bool,
                      account_id: str, exc):
    """R-6.53 崩溃重试：重开页面再试一次；仍失败则清理并抛第二次异常。

    触发背景：CFT 156 macOS 概率性崩溃，Nov 5 采集取证「导航 search_result 即
    Page crashed」——渲染进程崩溃后原 page 不可用，必须新开页面。
    """
    try:
        await _close_page_safe(page, account_id)
    except Exception:  # noqa: BLE001
        pass
    log.warning("页面加载遇崩溃（%s），重开页面重试: %s", str(exc)[:80], url[:80])
    page2 = await _page(account_id)
    try:
        return await _load_open(page2, url, wait_ms, show_front, suffix="_retry")
    except Exception as exc2:  # noqa: BLE001
        try:
            await _close_page_safe(page2, account_id)
        except Exception:  # noqa: BLE001
            pass
        raise exc2 from exc


async def _load(url: str, wait_ms: int = 5000, account_id: str = "default",
                show_front: bool = True):
    """打开页面并注入提取脚本（失败按 R-6.53 崩溃重试一次）。

    show_front=False（R-6.18）：采集后台完成——不 bring_to_front 抢前台，
    采集 tab 留在壳窗口 Tab2（设置页开关控制是否显示）。
    """
    page = await _page(account_id)
    try:
        return await _load_open(page, url, wait_ms, show_front)
    except Exception as exc:  # noqa: BLE001
        if _is_crash_error(exc):
            return await _load_retry(page, url, wait_ms, show_front, account_id, exc)
        try:
            await _close_page_safe(page, account_id)
        except Exception:  # noqa: BLE001
            pass
        raise

def _find_shell_tab(page, base: str):
    """R-6.37i 拆自 _switch_back_to_shell：定位壳窗口 Tab1（base 前缀、非当前页）。"""
    for p in page.context.pages:
        if not p.is_closed() and p != page and base and (p.url or "").startswith(base):
            return p
    return None


async def _switch_back_to_shell(page) -> None:
    """后台采集：窗口焦点回到壳窗口 Tab1（base_url 前缀 tab），采集 tab 保持存在。

    注：Chrome 单窗口仅一个 active tab——切换后采集 tab 为后台，若实测渲染节流
    导致采集 0 条，R-6.18 兜底由 collect_tab_visible=True（显示采集）承担。
    """
    try:
        base = (os.environ.get("XHS_SHELL_URL") or "").rstrip("/")
        shell = _find_shell_tab(page, base)
        if shell is not None:
            # R-6.53q：CFT 158 在后台 tab 上 Page.bringToFront 会永久挂起（CDP 假死
            # 直接触发点，Nov 5 DIAG 取证 goto 0.5s 后卡死）——cdp_call 绝对超时保护，
            # 焦点切换非关键路径，超时跳过。
            await cdp_call(shell.bring_to_front(), timeout=CDP_QUICK,
                           label="switch_back_to_shell", skip=True)
    except Exception:  # noqa: BLE001
        pass


async def login_check(account_id: str = "default") -> dict:
    """登录态检测：优先走无界面 headless 判定（不产生窗口/Dock 图标），失败回退账号引擎。"""
    try:
        return await _login_check_headless()
    except Exception as e:  # noqa: BLE001
        log.warning("headless 判定失败，回退浏览器: %s", e)
        page = await _load(LOGIN_URL, 6000, account_id)
        try:
            return await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            await _close_page_safe(page, account_id)


# ===========================================================================
# headless 登录判定（无窗口，不占用账号引擎）
# ===========================================================================
_h_pw = None
_h_browser = None


async def _headless_page():
    """无界面 Chromium（channel=chrome 无头模式）：不产生窗口、不进 Dock。"""
    global _h_pw, _h_browser
    from playwright.async_api import async_playwright
    if _h_pw is None:
        _h_pw = await async_playwright().start()
    if _h_browser is None or not _h_browser.is_connected():
        _h_browser = await _h_pw.chromium.launch(channel="chrome", headless=True)
    return _h_browser


async def _headless_explore(cookies: list[dict]) -> dict:
    """在无界面 Chromium 中：清 Cookie → 注入指定 Cookie → 打开小红书首页判定登录态。

    headless 下 explore 无 cookie 时会发生 SPA 重定向（evaluate 注入的函数会随旧文档丢失），
    因此用 add_init_script 在【每次文档加载前】自动注入提取脚本。
    """
    b = await _headless_page()
    ctx = b.contexts[0] if b.contexts else await b.new_context()
    try:
        await ctx.clear_cookies()
        await ctx.add_init_script(injections.get("extract"))
        if cookies:
            try:
                await ctx.add_cookies(cookies)
            except Exception:  # noqa: BLE001
                pass
        page = await ctx.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
            # 等 SPA 重定向/登录跳转稳定后再注入（goto 期间注入会随旧文档丢失）；
            # add_init_script 仅对【新文档】生效，故在此手动补注一次（幂等）
            await asyncio.sleep(5)
            await injections.inject_once(page, "extract")
            return await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            await ctx.remove_init_script(injections.get("extract"))
        except Exception:  # noqa: BLE001
            pass


def _saved_cookies(account_id: str = "default") -> list[dict]:
    """读该账号的本地 Cookie 文件（default 兼容旧单份 data/cookies.json）。"""
    try:
        eng = pool.peek(account_id)
        p = eng.cookie_file if eng else (config.DATA_DIR / "cookies.json")
        if p.exists():
            data = json.loads(p.read_text(encoding="utf8"))
            return data.get("cookies", []) or []
    except Exception:  # noqa: BLE001
        pass
    return []


async def _login_check_headless() -> dict:
    """headless 登录判定：自动带上 default 账号已保存 cookies.json（生产登录态）。"""
    return await _headless_explore(_saved_cookies("default"))


async def cookies(account_id: str = "default") -> dict:
    """读取账号引擎当前全部小红书 Cookie（CDP Network.getAllCookies）。"""
    eng = await _engine(account_id)
    xhs = await eng.read_cookies()
    ws = next((c.get("value", "") for c in xhs if c.get("name") == "web_session"), "")
    return {
        "count": len(xhs),
        "web_session": ws,
        "names": [c.get("name") for c in xhs],
        "top": [
            f"{c.get('domain')} {c.get('name')}={(c.get('value') or '')[:10]}…"
            for c in xhs[:15]
        ],
    }


async def verify_cookies(cookies_list: list[dict], account_id: str = "default") -> dict:
    """校验导入/读取的 Cookie：优先无界面 headless 判定（不弹窗口），失败回退账号引擎。"""
    try:
        r = await _headless_explore(cookies_list)
        if not r.get("url") or not isinstance(r.get("hasLoginUi"), bool):
            raise RuntimeError("headless 判定结果异常")
        return r
    except Exception as e:  # noqa: BLE001
        log.warning("headless 校验失败，回退引擎: %s", e)
        return await _verify_profile(cookies_list, account_id)


async def _verify_profile(cookies_list: list[dict], account_id: str = "default") -> dict:
    """（回退路径）在账号引擎中【先清空已有 Cookie】→ 只注入待校验 Cookie →
    打开小红书首页判定登录态 → 恢复本地已保存 Cookie。

    必须清空的原因：引擎 context 可能残留此前注入的有效 Cookie；不清空直接注入校验，
    explore 会带着旧登录态判定「已登录」，导致校验失效（实测缺陷）。
    """
    ctx = await _ctx(account_id)
    page = await ctx.new_page()  # 新开 tab：不占用现有 tab（含 APP 前端页）
    cdp = await ctx.new_cdp_session(page)
    try:
        await cdp.send("Network.clearBrowserCookies")
        try:
            await ctx.add_cookies(cookies_list)
        except Exception:  # noqa: BLE001
            await cdp.send("Network.setCookies", {"cookies": cookies_list})
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
            # 同 _headless_explore：等 SPA 重定向稳定后再注入，避免函数随旧文档丢失
            await asyncio.sleep(5)
            await injections.inject_once(page, "extract")
            result = await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            await _close_page_safe(page, account_id)
        return result
    finally:
        # 恢复生产登录态：清空 → 重新注入本地已保存 Cookie（default 兼容旧单份）
        try:
            await cdp.send("Network.clearBrowserCookies")
            saved = _saved_cookies(account_id)
            if saved:
                await ctx.add_cookies(saved)
        except Exception:  # noqa: BLE001
            pass


# ===========================================================================
# 登录墙 / 筛选 / 搜索
# ===========================================================================
def _wall_by_url(url: str) -> str:
    """按页面 URL 判定拦截原因（纯函数，便于单测与控圈复杂度）。

    R-6.53z6：平台人机验证页实测不带 `/login`（地址是
    `/website-login/captcha?...`），原判定只看 `/login` 与页面中文文案，
    两者都不命中 → 采集 0 条被误报成「限流」，用户不知道要去过验证。
    """
    if "/login" in url:
        return "页面已跳转登录（小红书会话失效，请重新扫码或导入 Cookie）"
    if "captcha" in url or "website-login" in url:
        return "页面跳转小红书人机验证（请在采集窗完成验证后重试）"
    return ""


async def _detect_login_wall(page) -> str:
    """检测搜索页是否被「登录墙 / 风控验证」拦截。返回原因描述；页面正常返回 ""。"""
    try:
        wall = _wall_by_url(page.url or "")
        if wall:
            return wall
        text = await cdp_call(
            page.evaluate("() => document.body ? document.body.innerText.slice(0, 1500) : ''"),
            timeout=CDP_EVAL, label="login_wall.text", skip=True)  # R-6.53s：引擎卡死不阻塞登录墙检测
        text = text or ""
    except Exception:  # noqa: BLE001
        return ""
    for kw in ("扫码登录", "登录后查看", "请先登录", "完成验证", "安全验证", "验证码"):
        if kw in text:
            return f"页面出现「{kw}」（Cookie 有效但被小红书风控拦截，需人工扫码/验证后重试）"
    return ""


async def _wait_note_cards(page, timeout_s: float = 45) -> tuple[bool, str]:
    """等待搜索页出现笔记卡片（section.note-item）。返回 (是否出现, 登录墙原因)。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        wall = await _detect_login_wall(page)
        if wall:
            return False, wall
        try:
            n = await cdp_call(
                page.evaluate("() => document.querySelectorAll('section.note-item').length"),
                timeout=CDP_EVAL, label="wait_cards.evaluate", skip=True)
            n = n or 0
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 引擎卡死时 6s 超时，不空等
            n = 0
        if n > 0:
            return True, ""
        await asyncio.sleep(2.5)
    return False, ""


async def _apply_filters(page, filters: dict):
    """搜索页应用筛选（排序 / 笔记类型 / 发布时间 / 搜索范围 / 位置距离）。

    跳过默认值（综合 / 全部 / 不限）：只在用户明确选择非默认条件时点击对应选项。
    点击条件后页面会刷新，每项点击后等待结果重新加载。
    """
    wanted = [
        (label, val)
        for label, val in [
            ("排序依据", filters.get("sort")),
            ("笔记类型", filters.get("note_type")),
            ("发布时间", filters.get("time_range")),
            ("搜索范围", filters.get("scope")),
            ("位置距离", filters.get("distance")),
        ]
        if val and val not in ("综合", "全部", "不限", "")
    ]
    if not wanted:
        return
    for _label, val in wanted:
        await page.evaluate("""() => {
            const el = [...document.querySelectorAll('span, div')].find(x => (x.innerText || '').trim() === '筛选');
            if (el) el.click();
        }""")
        await asyncio.sleep(1.2)
        await page.evaluate(
            f"""() => {{
                const els = [...document.querySelectorAll('div.tags, div.channel')];
                const t = els.find(x => (x.innerText || '').trim() === {json.dumps(val, ensure_ascii=False)});
                if (t) {{ t.click(); return true; }}
                return false;
            }}"""
        )
        await asyncio.sleep(2.5)
    await page.evaluate("""() => {
        const el = [...document.querySelectorAll('span, div')].find(x => (x.innerText || '').trim() === '收起');
        if (el) el.click();
    }""")
    await asyncio.sleep(1.2)


def _handle_login_wall(account_id: str, wall: str) -> None:
    """登录墙处置（R-6.22i：统一走软风控，不锁账号）。

    历史背景（已修复 `e2ec0cc`，2026-11-06 复核本函数只调 mark_manual）：R-6.15
    拍板移除风控熔断后，曾有 24h 硬熔断（mark_hard→cooldown）残留在登录墙路径，
    导致 _require_login 403「请 X 小时后再试」把账号锁死。
    现统一 mark_manual：任务中止但账号不熔断，页面保留供扫码/验证；
    登录成功后 login_status 自动 resolve，下个任务直接继续。
    """
    risk.mark_manual(account_id, reason=wall)


def _check_login_wall(account_id: str, wall: str) -> None:
    """登录墙/风控验证命中即中止：软风控标记 + 抛错（页面保留供扫码）。"""
    if not wall:
        return
    _handle_login_wall(account_id, wall)  # 统一软风控 → 引导扫码恢复（R-6.22i）
    raise BrowserLoginRequiredError(wall)  # 保留页面供扫码，不关闭


LOGIN_URL = "https://www.xiaohongshu.com/login"

# R-6.53z8（用户拍板，Nov 6）：除登录页外**不允许用 URL 进入任何页面**，也不允许
# 刷新页面——实测首页刷新/直连搜索页 URL 更容易被要求登录或弹风控。搜索、进详情、
# 进作者页一律模拟真人操作（点搜索框 → 逐字键入 → 回车 / 点卡片）。登录态不确定时
# 走登录页间接进入：登录页在已登录情况下会自己跳到 explore，我们不插手。
_SEARCH_BOX_JS = """() => {
  const el = document.querySelector('#search-input') || document.querySelector('input.search-input');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""

_NEEDS_LOGIN_JS = """() => {
  const ph = [...document.querySelectorAll('input')].map(i => i.placeholder || '');
  const txt = document.body ? document.body.innerText : '';
  return ph.some(p => p.indexOf('手机号') >= 0) || txt.indexOf('登录探索更多内容') >= 0;
}"""


async def _open_login_page(page, timeout_s: float = 30) -> bool:
    """唯一允许的 URL 导航：打开 /login，等平台自己跳到 explore。返回是否已进浏览页。

    已登录 → 登录页自己跳 explore（平台行为）；未登录 → 停在登录页，返回 False
    （由调用方抛 BrowserLoginRequiredError 提示人工扫码/验证）。
    """
    await cdp_call(page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000),
                   timeout=CDP_NAV, label="open_login")
    for _ in range(max(1, int(timeout_s / 2))):
        await asyncio.sleep(2)
        try:
            url = page.url or ""
        except Exception:  # noqa: BLE001（页面已毁：交给上层重试）
            return False
        if "xiaohongshu.com" in url and "/login" not in url and "captcha" not in url:
            return True
    return False


async def _page_needs_login(page) -> bool:
    """页面是否处于未登录渲染态（有手机号输入框/「登录探索更多内容」）。

    Cookie 存在不代表页面已按登录态渲染：实测登录态页面滞后时会整页按游客渲染，
    此时点搜索框不触发搜索（只会弹登录浮层）。
    """
    try:
        return bool(await cdp_call(page.evaluate(_NEEDS_LOGIN_JS),
                                   timeout=CDP_EVAL, label="needs_login", skip=True))
    except Exception:  # noqa: BLE001（页面未就绪：按需要登录处理）
        return True


async def _ensure_browse_page(page, account_id: str) -> bool:
    """确保页面处于登录态浏览页；不确定就走 /login（唯一允许 URL 的页面）。"""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    on_browse = "xiaohongshu.com" in url and "/login" not in url and "captcha" not in url
    if on_browse and not await _page_needs_login(page):
        return True
    return await _open_login_page(page)


async def _search_by_typing(page, keyword: str, account_id: str) -> bool:
    """模拟真人搜索：点搜索框 → 逐字输入 → 回车 → 等卡片。不碰 URL、不刷新。"""
    box = await cdp_call(page.evaluate(_SEARCH_BOX_JS), timeout=CDP_EVAL, label="search.box")
    if not box:
        return False
    await cdp_call(page.mouse.click(box["x"], box["y"]), timeout=CDP_QUICK, label="search.click")
    await asyncio.sleep(0.8)
    await cdp_call(page.keyboard.type(keyword, delay=90), timeout=CDP_EVAL, label="search.type")
    await asyncio.sleep(0.5)
    await cdp_call(page.keyboard.press("Enter"), timeout=CDP_QUICK, label="search.enter")
    appeared, wall = await _wait_note_cards(page, timeout_s=20)
    _check_login_wall(account_id, wall)
    return appeared


async def _prepare_search_page(keyword: str, filters: dict | None = None,
                               account_id: str = "default"):
    """进入搜索页并等卡片渲染——只靠页面内交互，不用 URL、不刷新。

    路径：取新页 → 进 /login（已登录则平台自己跳 explore）→ 点搜索框输入关键词回车。
    未登录/停在登录页 → 抛 BrowserLoginRequiredError（页面保留供扫码/验证）。
    空结果重试一次（重新点框输入），仍空则按 0 条收尾，不再 reload。
    """
    page = await _page(account_id)
    if not await _ensure_browse_page(page, account_id):
        raise BrowserLoginRequiredError(
            "小红书要求登录（请在采集窗完成扫码/人机验证后重试）")
    appeared = await _search_by_typing(page, keyword, account_id)
    if not appeared:
        appeared = await _search_by_typing(page, keyword, account_id)
    _remember_page(page, account_id)
    if appeared:
        await _apply_filters(page, filters or {})
        if filters:
            _, wall = await _wait_note_cards(page, timeout_s=20)
            _check_login_wall(account_id, wall)
    return page


async def _drain_page(page, items: list, seen: set) -> int:
    """提取当前页可见卡片并入 items（广告卡过滤 + 去重）。返回新增条数。"""
    await injections.inject_once(page, "extract")  # 注册表幂等注入：页面已注入则跳过
    got = await page.evaluate("() => xhsExtractSearchNotes()")
    added = 0
    for it in got:
        k = it.get("feed_id") or it.get("id")
        # 过滤推广/广告卡片：真实笔记 id 是 24 位 hex（推广位是 UUID 等其他格式）
        if not re.fullmatch(r"[0-9a-f]{24}", str(k or "")):
            continue
        if k not in seen:
            seen.add(k)
            items.append(it)
            added += 1
    return added


def _should_stop_collect(count: int, limit: int, stale: int, added: int, should_stop) -> tuple[bool, int]:
    """滚动采集终止判定：采满 / 用户停止 / 页面不再增长（stale≥2 且有结果）。"""
    if count >= limit:
        return True, stale
    if should_stop and should_stop():
        return True, stale
    if added == 0:
        stale += 1
        # 保持原设计：count=0 不提前停（单测 test_no_results_no_stop 断言）——
        # 首屏慢加载场景继续滚动给机会；空壳场景由 _prepare_search_page 的
        # R-6.53u reload 重试兜底，真 0 条由调用方提示「未采集到数据」。
        if stale >= 2 and count > 0:
            return True, stale
    else:
        stale = 0
    return False, stale


async def _open_search_page(keyword: str, filters, account_id: str,
                            label: str = "prepare_search_page"):
    """准备搜索页：cdp_call 绝对超时保护（R-6.53s，引擎假死时内部 timeout 不触发）。"""
    return await cdp_call(_prepare_search_page(keyword, filters, account_id),
                          timeout=CDP_PREP, label=label)


async def _search_prepare(keyword: str, filters, account_id: str):
    """清残留页 + 打开搜索页；CDP 假死超时返回 None（调用方按空结果收尾）。"""
    try:
        await _cleanup_stale_xhs_pages(account_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        return await _open_search_page(keyword, filters, account_id)
    except CdpTimeoutError:
        # 引擎 CDP 假死（search_result 页拖死）时绝对超时强制打断——否则 worker
        # 卡在准备阶段永不返回，用户点停止迟迟不停。返回 None 由调用方收尾。
        return None


async def _search_recover(page, keyword: str, filters, account_id: str,
                          exc, kind: str, tries: int, max_tries: int):
    """R-6.53 采集页崩溃恢复：重开搜索页返回新 page；非崩溃/超限/重开失败返回 None。

    tries 为已用重开次数（调用方负责递增），语义与原 crash_tries 上限一致。
    """
    if not (_is_crash_error(exc) and tries < max_tries):
        return None
    log.warning("%s崩溃（%s），重开搜索页继续（第 %d/%d 次）",
                kind, str(exc)[:80], tries + 1, max_tries)
    try:
        await _close_page_safe(page, account_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        return await _open_search_page(keyword, filters, account_id,
                                       label="prepare_search_page_retry")
    except Exception:  # noqa: BLE001
        return None  # 重开失败：调用方收尾返回已采


async def _search_round(page, items: list, seen: set, limit: int, stale: int,
                        should_stop, on_progress) -> tuple[int, int, bool]:
    """一轮：提取新卡片 → 播报进度 → 终止判定 → 滚动下一页。

    返回 (added, stale, done)。done=True 表示本轮后收尾（采满/用户停止/不再增长）。
    假死与崩溃异常直接上抛，由 `_search_loop` 统一处置。
    """
    added = await cdp_call(_drain_page(page, items, seen),
                           timeout=CDP_DRAIN, label="drain_page")
    if on_progress:
        # R-6.53v：回调携带 items 引用——任务级取消收尾时，worker 据此
        # 抢救已采数据（搜索阶段未返回即被 cancel，items 是唯一数据源）
        on_progress(len(items), items)
    done, stale = _should_stop_collect(len(items), limit, stale, added, should_stop)
    if done:
        return added, stale, True
    try:
        await cdp_call(page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)"),
                       timeout=CDP_DRAIN, label="scroll_more")
    except CdpTimeoutError:
        raise
    except Exception as exc:  # noqa: BLE001
        setattr(exc, "_xhs_stage", "滚动")  # 崩溃归因：区分提取阶段与滚动阶段
        raise
    return added, stale, False


async def _search_loop(page, keyword: str, filters, account_id: str, items: list,
                       seen: set, limit: int, stale: int, should_stop, on_progress,
                       max_crash_tries: int = 2):
    """滚动采集循环（120s 上限）：捕获 CDP 假死与渲染进程崩溃。

    返回 (page, stale)。崩溃重开成功时返回新 page；重开失败或假死收尾返回
    (None 或原 page) ——调用方据 page 是否为 None 决定是否还要关闭。
    """
    crash_tries = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            _added, stale, done = await _search_round(
                page, items, seen, limit, stale, should_stop, on_progress)
        except CdpTimeoutError:
            return page, stale  # R-6.53s 假死：绝对超时打断收尾，不空等
        except Exception as exc:  # noqa: BLE001
            page = await _search_recover(page, keyword, filters, account_id, exc,
                                         getattr(exc, "_xhs_stage", "采集页"),
                                         crash_tries, max_crash_tries)
            if page is None:
                return None, stale
            crash_tries += 1
            continue
        if done:
            return page, stale
        await asyncio.sleep(1.8)
    return page, stale


async def search(keyword: str, limit: int = 30, on_progress=None, should_stop=None,
                 filters: dict | None = None, account_id: str = "default") -> dict:
    """任务式采集：滚动平台搜索页逐批提取，支持去重合并、进度回调与中途停止。

    - on_progress(n, items)：每批提取后回调已采集数量与 items 引用
    - should_stop()：返回 True 时提前收尾（用户点「停止采集」）
    - filters：搜索筛选（排序/笔记类型/发布时间/搜索范围/位置距离）
    - account_id：使用哪个账号引擎采集（默认 default）
    - 总时长上限 120s：平台 AI 搜索页渲染慢时的兜底，超时返回已采集部分
    - R-6.53：渲染进程崩溃（Page crashed / Target closed）时自动重开搜索页继续，
      最多 2 次；已采 items 保留。
    """
    page = await _search_prepare(keyword, filters, account_id)
    if page is None:
        return {"keyword": keyword, "count": 0, "items": [],
                "stopped": bool(should_stop and should_stop())}
    items: list = []
    seen: set = set()
    try:
        page, _stale = await _search_loop(page, keyword, filters, account_id,
                                          items, seen, limit, 0, should_stop, on_progress)
        return {"keyword": keyword, "count": len(items), "items": items,
                "stopped": bool(should_stop and should_stop())}
    finally:
        _forget_page(account_id)
        if page is not None:
            await _close_page_safe(page, account_id)

# ===========================================================================
# 详情 / 作者 / 评论
# ===========================================================================
# --------------------------------------------------------------------------
# R-6.53z8：详情/作者/评论一律"点开→提取→返回"，禁止另行 URL 进入
# --------------------------------------------------------------------------
_ACTIVE_PAGES: dict[str, object] = {}

_CLICK_NOTE_JS = """(fid) => {
  const links = [...document.querySelectorAll('section.note-item a')];
  const hit = links.find(a => ((a.getAttribute('href') || '') + '').indexOf(fid) >= 0);
  if (!hit) return false;
  hit.click();
  return true;
}"""

_CLICK_AUTHOR_JS = """(uid) => {
  const links = [...document.querySelectorAll('a[href*="/user/profile/"]')];
  const hit = links.find(a => ((a.getAttribute('href') || '') + '').indexOf(uid) >= 0);
  if (!hit) return false;
  hit.click();
  return true;
}"""

_BACK_JS = """() => {
  const cands = [...document.querySelectorAll('div, span, button, [role="button"]')]
    .filter(e => e.offsetWidth && (e.innerText || '').trim().length <= 4
                 && /返回|关闭/.test((e.innerText || '').trim()));
  const hit = cands[0];
  if (!hit) return false;
  hit.click();
  return true;
}"""


def _remember_page(page, account_id: str = "default") -> None:
    """记下当前采集搜索页：详情/作者/评论只能在这个页面上点开，不新开 URL。"""
    _ACTIVE_PAGES[account_id] = page


def _forget_page(account_id: str = "default") -> None:
    _ACTIVE_PAGES.pop(account_id, None)


def _active_page(account_id: str = "default"):
    """取活动搜索页；已关闭视为没有（调用方按缺数据降级，不回退到 URL）。"""
    page = _ACTIVE_PAGES.get(account_id)
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:  # noqa: BLE001
        return None
    return None


async def _back_to_list(page) -> None:
    """从详情页返回结果页：先点页面返回/关闭按钮，退化历史后退；不刷新、不输 URL。"""
    clicked = await cdp_call(page.evaluate(_BACK_JS), timeout=CDP_QUICK,
                             label="back.click", skip=True)
    if clicked:
        await asyncio.sleep(1.2)
        return
    try:
        await cdp_call(page.go_back(), timeout=CDP_NAV, label="back.history", skip=True)
    except Exception:  # noqa: BLE001
        pass


async def note(note_id: str, xsec_token: str = "", wait_ms: int = 5000,
               account_id: str = "default") -> dict:
    """详情页数据：在采集搜索页里点开目标卡片提取（不用 URL、不刷新）。

    没有活动搜索页（采集已结束/被关）时返回 {}，由调用方按缺字段降级——
    绝不为了补字段另开 /explore/{id} 或 /search_result/{id}。
    """
    page = _active_page(account_id)
    if page is None:
        log.info("无活动搜索页，跳过详情 note_id=%s（规则禁止 URL 进详情页）", note_id)
        return {}
    hit = await cdp_call(page.evaluate(_CLICK_NOTE_JS, note_id),
                         timeout=CDP_EVAL, label="note.click")
    if not hit:
        return {}
    await asyncio.sleep(wait_ms / 1000)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _back_to_list(page)


async def note_url(url: str, wait_ms: int = 5000, account_id: str = "default") -> dict:
    """按完整链接打开笔记页并提取详情（**唯一例外**：链接由用户本人提供，无页内入口可达）。"""
    page = await _load(url, wait_ms, account_id)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _close_page_safe(page, account_id)


async def user(uid: str, wait_ms: int = 5000, account_id: str = "default") -> dict:
    """作者主页数据：在详情页点作者链接进入（不用 URL、不刷新）；无活动页返回 {}。"""
    page = _active_page(account_id)
    if page is None:
        log.info("无活动搜索页，跳过作者页 uid=%s", uid)
        return {}
    hit = await cdp_call(page.evaluate(_CLICK_AUTHOR_JS, uid),
                         timeout=CDP_EVAL, label="user.click")
    if not hit:
        return {}
    await asyncio.sleep(wait_ms / 1000)
    try:
        return await page.evaluate("() => xhsExtractUser()")
    finally:
        await _back_to_list(page)


async def clients(note_id: str, xsec_token: str = "", account_id: str = "default") -> dict:
    """评论区互动用户：在采集页点开目标卡片 → 展开/滚动评论区 → 提取 → 返回。"""
    page = _active_page(account_id)
    if page is None:
        log.info("无活动搜索页，跳过评论采集 note_id=%s", note_id)
        return {}
    hit = await cdp_call(page.evaluate(_CLICK_NOTE_JS, note_id),
                         timeout=CDP_EVAL, label="clients.click")
    if not hit:
        return {}
    await asyncio.sleep(4)
    try:
        return await _drain_comments(page)
    finally:
        await _back_to_list(page)


async def _drain_comments(page) -> dict:
    """评论区懒加载：滚动容器 + 点「展开/全部评论」，再提取（沿用原实现）。"""
    for _ in range(3):
        await page.evaluate(_COMMENT_SCROLL_JS)
        await asyncio.sleep(1.2)
    for _ in range(2):
        clicked = await page.evaluate(_COMMENT_EXPAND_JS)
        if clicked:
            await asyncio.sleep(2.5)
            await page.evaluate(_COMMENT_SCROLL_JS)
            await asyncio.sleep(2)
        else:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
    return await page.evaluate("() => xhsExtractComments()")


_COMMENT_SCROLL_JS = """() => {
  const c = document.querySelector('.interaction-container') || [...document.querySelectorAll('[class*="comment"]')].pop();
  if (c) c.scrollIntoView({ block: "center" });
}"""

_COMMENT_EXPAND_JS = """() => {
  const btns = [...document.querySelectorAll('button, [role="button"], div, span')].filter(e => {
    const t = (e.innerText || '').trim();
    return t.length < 20 && /(展开|查看)?全部评论|查看更多评论/.test(t);
  });
  for (const b of btns) {
    if (b.tagName === 'BUTTON' || b.getAttribute('role') === 'button' || b.childElementCount === 0) {
      try { b.click(); return true; } catch (e) { return false; }
    }
  }
  return false;
}"""


# ===========================================================================
# 引擎生命周期（server shutdown / 一次性动作）
# ===========================================================================

async def stop():
    """关闭全部引擎 + Playwright 实例（后端退出钩子）。"""
    await pool.close_all()

