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


async def _cleanup_stale_xhs_pages(account_id: str = "default") -> int:
    """R-6.53：采集前清理残留的小红书搜索/探索页（登录墙页保留）。

    取证（Nov 5）：历史采集崩溃/中断后残留 search_result 页会僵死（CDP WS 无响应）
    且 cleanup_tabs 按设计保留所有 xiaohongshu.com tab → 页面累积放大引擎负担，
    与崩溃相互促进。每次采集前清掉非登录墙的旧 xhs 页，降低累积触发面。
    """
    try:
        eng = await _engine(account_id)
        ctx = await eng.context()
        closed = 0
        for p in list(ctx.pages):
            u = p.url or ""
            if (
                "xiaohongshu.com" in u
                and "/website-login" not in u
                and "captcha" not in u
            ):
                try:
                    await p.close()
                    closed += 1
                except Exception:  # noqa: BLE001
                    pass
        if closed:
            log.info("采集前清理残留 xhs 页 %d 个", closed)
        return closed
    except Exception:  # noqa: BLE001
        return 0






# ===========================================================================
# 页面加载与提取
# ===========================================================================
async def _load(url: str, wait_ms: int = 5000, account_id: str = "default",
                show_front: bool = True):
    """打开页面并注入提取脚本。show_front=False（R-6.18）：采集后台完成——
    不 bring_to_front 抢前台，采集 tab 留在壳窗口 Tab2（设置页开关控制是否显示）。
    """
    page = await _page(account_id)
    try:
        # R-6.53s：统一 CDP 超时保护——引擎假死时 Playwright 内部 timeout 不触发
        # （CDP 无响应），cdp_call 以绝对超时强制打断并打 WARNING 溯源。
        await cdp_call(page.goto(url, wait_until="domcontentloaded", timeout=12000),
                       timeout=CDP_NAV, label="load.goto")
        try:
            if show_front:
                await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                               label="load.bring_to_front", skip=True)
            else:
                # 后台采集：切回壳窗口 Tab1（前端/登录 tab），采集 tab 保持激活渲染
                await _switch_back_to_shell(page)
        except Exception:  # noqa: BLE001
            pass
        await injections.inject_once(page, "extract")
        await asyncio.sleep(wait_ms / 1000)
        return page
    except Exception as exc:  # noqa: BLE001
        # R-6.53：渲染进程崩溃（Page crashed / Target closed）时重开页面重试一次
        # （CFT 156 macOS 概率性崩溃，Nov 5 采集取证：导航 search_result 即 Page crashed）。
        try:
            await _close_page_safe(page, account_id)
        except Exception:  # noqa: BLE001
            pass
        if _is_crash_error(exc):
            log.warning("页面加载遇崩溃（%s），重开页面重试: %s", str(exc)[:80], url[:80])
            page2 = await _page(account_id)
            try:
                await cdp_call(page2.goto(url, wait_until="domcontentloaded", timeout=12000),
                               timeout=CDP_NAV, label="load.goto_retry")
                try:
                    if show_front:
                        await cdp_call(page2.bring_to_front(), timeout=CDP_QUICK,
                                       label="load.bring_to_front_retry", skip=True)
                    else:
                        await _switch_back_to_shell(page2)
                except Exception:  # noqa: BLE001
                    pass
                await injections.inject_once(page2, "extract")
                await asyncio.sleep(wait_ms / 1000)
                return page2
            except Exception as exc2:  # noqa: BLE001
                try:
                    await _close_page_safe(page2, account_id)
                except Exception:  # noqa: BLE001
                    pass
                raise exc2 from exc
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
        page = await _load(_XHS + "/explore", 6000, account_id)
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
            await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
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
            await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
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
async def _detect_login_wall(page) -> str:
    """检测搜索页是否被「登录墙 / 风控验证」拦截。返回原因描述；页面正常返回 ""。"""
    try:
        if "/login" in page.url:
            return "页面已跳转登录（小红书会话失效，请重新扫码或导入 Cookie）"
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

    R-6.15 拍板移除拟人频率保护/风控熔断/人工锁定后，24h 硬熔断（mark_hard→cooldown）
    仍残留于登录墙处置路径：非软风控词集 → mark_hard → 后续 _require_login 403
    「请 X 小时后再试」，账号被锁——拍板未全量落地（老问题重现根因）。
    现统一走 mark_manual：任务中止但账号不熔断，页面保留供扫码/验证；
    登录成功后 login_status 自动 resolve，下个任务直接继续。
    """
    risk.mark_manual(account_id, reason=wall)


async def _prepare_search_page(keyword: str, filters: dict | None = None,
                               account_id: str = "default"):
    """打开搜索页并等卡片渲染；新版 AI 搜索页（无 note-item）回退旧版 URL。返回可用 page。

    检测登录墙/风控验证：Cookie 在但页面要求登录或验证时，立即抛
    BrowserLoginRequiredError（不空等、不空跑），由采集链路中止任务并提示人工处理。
    **登录墙页面保留不关闭**：用户需要在这个页面上扫码/验证恢复。
    """
    url = _XHS + "/search_result?keyword=" + _urlencode(keyword) + "&source=web_search_result_notes"
    page = await _load(url, 2500, account_id,
                       show_front=config.get_config().get("collect_tab_visible", False))
    appeared, wall = await _wait_note_cards(page, timeout_s=20)
    if wall:
        _handle_login_wall(account_id, wall)  # 软→引导扫码恢复；硬→熔断
        raise BrowserLoginRequiredError(wall)  # 保留页面供扫码，不关闭
    if not appeared:
        await _close_page_safe(page, account_id)
        page = await _load(_XHS + "/search_result?keyword=" + _urlencode(keyword), 2500, account_id,
                           show_front=config.get_config().get("collect_tab_visible", False))
        appeared, wall = await _wait_note_cards(page, timeout_s=20)
        if wall:
            _handle_login_wall(account_id, wall)
            raise BrowserLoginRequiredError(wall)
    if appeared:
        await _apply_filters(page, filters or {})
        if filters:
            _, wall = await _wait_note_cards(page, timeout_s=20)
            if wall:
                _handle_login_wall(account_id, wall)
                raise BrowserLoginRequiredError(wall)
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
        if stale >= 2 and count > 0:
            return True, stale
    else:
        stale = 0
    return False, stale


async def search(keyword: str, limit: int = 30, on_progress=None, should_stop=None,
                 filters: dict | None = None, account_id: str = "default") -> dict:
    """任务式采集：滚动平台搜索页逐批提取，支持去重合并、进度回调与中途停止。

    - on_progress(n)：每批提取后回调已采集数量
    - should_stop()：返回 True 时提前收尾（用户点「停止采集」）
    - filters：搜索筛选（排序/笔记类型/发布时间/搜索范围/位置距离）
    - account_id：使用哪个账号引擎采集（默认 default）
    - 总时长上限 120s：平台 AI 搜索页渲染慢时的兜底，超时返回已采集部分
    - R-6.53：渲染进程崩溃（Page crashed / Target closed）时自动重开搜索页继续，
      最多 2 次；已采 items 保留。崩溃吸收见 _is_crash_error。
    """
    crash_tries = 0
    max_crash_tries = 2
    try:
        await _cleanup_stale_xhs_pages(account_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        page = await cdp_call(_prepare_search_page(keyword, filters, account_id),
                              timeout=CDP_PREP, label="prepare_search_page")
    except CdpTimeoutError:
        # R-6.53s：引擎 CDP 假死（search_result 页拖死）时绝对超时强制打断——否则 worker
        # 卡在准备阶段永不返回，用户点停止迟迟不停。返回空结果收尾，状态由调用方判定。
        return {"keyword": keyword, "count": 0, "items": [], "stopped": bool(should_stop and should_stop())}
    items: list = []
    seen: set = set()
    stale = 0
    deadline = time.time() + 120
    try:
        while time.time() < deadline:
            try:
                added = await cdp_call(_drain_page(page, items, seen),
                                       timeout=CDP_DRAIN, label="drain_page")
            except CdpTimeoutError:
                # R-6.53s：引擎卡死（CDP 无响应）时绝对超时打断收尾——否则 worker
                # 卡在无响应的 evaluate 上，用户点停止迟迟不停、任务永不退出。
                break
            except Exception as exc:  # noqa: BLE001
                # R-6.53：渲染进程崩溃→重开搜索页继续（保留已采 items）
                if _is_crash_error(exc) and crash_tries < max_crash_tries:
                    crash_tries += 1
                    log.warning("采集页崩溃（%s），重开搜索页继续（第 %d/%d 次）",
                                str(exc)[:80], crash_tries, max_crash_tries)
                    try:
                        await _close_page_safe(page, account_id)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        page = await cdp_call(
                            _prepare_search_page(keyword, filters, account_id),
                            timeout=CDP_PREP, label="prepare_search_page_retry")
                    except Exception:  # noqa: BLE001
                        break  # 重开失败，收尾返回已采
                    continue
                break
            if on_progress:
                on_progress(len(items))
            done, stale = _should_stop_collect(len(items), limit, stale, added, should_stop)
            if done:
                break
            try:
                await cdp_call(page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)"),
                               timeout=CDP_DRAIN, label="scroll_more")
            except CdpTimeoutError:
                break
            except Exception as exc:  # noqa: BLE001
                if _is_crash_error(exc) and crash_tries < max_crash_tries:
                    crash_tries += 1
                    log.warning("滚动崩溃（%s），重开搜索页继续（第 %d/%d 次）",
                                str(exc)[:80], crash_tries, max_crash_tries)
                    try:
                        await _close_page_safe(page, account_id)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        page = await cdp_call(
                            _prepare_search_page(keyword, filters, account_id),
                            timeout=CDP_PREP, label="prepare_search_page_retry")
                    except Exception:  # noqa: BLE001
                        break
                    continue
                break
            await asyncio.sleep(1.8)
        stopped = bool(should_stop and should_stop())
        return {"keyword": keyword, "count": len(items), "items": items, "stopped": stopped}
    finally:
        await _close_page_safe(page, account_id)


# ===========================================================================
# 详情 / 作者 / 评论
# ===========================================================================
async def note(note_id: str, xsec_token: str = "", wait_ms: int = 5000,
               account_id: str = "default") -> dict:
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, wait_ms, account_id)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _close_page_safe(page, account_id)


async def note_url(url: str, wait_ms: int = 5000, account_id: str = "default") -> dict:
    """按完整链接打开笔记页并提取详情（数据回流用：用户发布后的笔记链接）。"""
    page = await _load(url, wait_ms, account_id)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _close_page_safe(page, account_id)


async def user(uid: str, wait_ms: int = 5000, account_id: str = "default") -> dict:
    page = await _load(f"{_XHS}/user/profile/{uid}", wait_ms, account_id)
    try:
        return await page.evaluate("() => xhsExtractUser()")
    finally:
        await _close_page_safe(page, account_id)


async def clients(note_id: str, xsec_token: str = "", account_id: str = "default") -> dict:
    """采集笔记评论区互动用户名单：打开原作品页 → 等待评论区渲染 → DOM 提取。"""
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, 6000, account_id)
    try:
        # 评论区懒加载：滚动到精确容器 .interaction-container 触发渲染
        for _ in range(3):
            await page.evaluate("""() => {
              const c = document.querySelector('.interaction-container') || [...document.querySelectorAll('[class*="comment"]')].pop();
              if (c) c.scrollIntoView({ block: "center" });
            }""")
            await asyncio.sleep(1.2)
        # 评论区默认折叠：尝试点击「展开/查看全部评论」类按钮；无按钮则滚动到底加载
        for _ in range(2):
            clicked = await page.evaluate("""() => {
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
            }""")
            if clicked:
                await asyncio.sleep(2.5)
                await page.evaluate("""() => {
                  const c = document.querySelector('.interaction-container') || [...document.querySelectorAll('[class*="comment"]')].pop();
                  if (c) c.scrollIntoView({ block: "center" });
                }""")
                await asyncio.sleep(2)
            else:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
        return await page.evaluate("() => xhsExtractComments()")
    finally:
        await _close_page_safe(page, account_id)



# ===========================================================================
# 引擎生命周期（server shutdown / 一次性动作）
# ===========================================================================

async def stop():
    """关闭全部引擎 + Playwright 实例（后端退出钩子）。"""
    await pool.close_all()


def _urlencode(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)
