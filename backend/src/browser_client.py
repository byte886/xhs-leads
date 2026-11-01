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

from src import config, engine_pool, logger as xlog, risk
from src.engine_pool import pool

log = xlog.get("browser")

if getattr(sys, "_MEIPASS", None):
    # 打包态（PyInstaller macOS）：资源随包进 Contents/Resources
    _ROOT = pathlib.Path(sys._MEIPASS).parent / "Resources"
else:
    _ROOT = pathlib.Path(__file__).resolve().parents[2]  # backend/src -> 项目根
_EXTRACT_SRC = (_ROOT / "extension" / "lib" / "extract.js").read_text(encoding="utf8")
_PRESS_SCRIPT = _ROOT / "scripts" / "browser" / "cdp" / "press_allow.applescript"
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


# ===========================================================================
# Chrome 发现 / CDP 配置（保留：CDP 面板接口仍在使用；B 方案引擎已不依赖）
# ===========================================================================
def save_cdp_config(chrome_path: str = "", profile_dir: str = "") -> dict:
    """保存用户自定义 Chrome 路径 / Profile 路径（持久化，兼容旧 cdp_config.json）。"""
    cfg = config.get_config()
    if chrome_path:
        cfg["chrome_path"] = config.home_relative(chrome_path)
        config.set_config("chrome_path", cfg["chrome_path"])
    if profile_dir:
        cfg["profile_dir"] = config.home_relative(profile_dir)
        config.set_config("profile_dir", cfg["profile_dir"])
    return {"chrome_path": cfg.get("chrome_path", ""), "profile_dir": cfg.get("profile_dir", "")}


def chrome_executable() -> str | None:
    """定位引擎可执行文件：内置 CFT（Chrome for Testing，独立应用身份，不占用
    用户日常 Chrome 的 LaunchServices 单实例）优先 → 用户自定义 → 平台探测（系统
    Chrome 兜底）。R-6.11：内置 CFT 解决「APP 运行时日常 Chrome 无法新建窗口」。
    """
    builtin = _builtin_cft_candidates()
    if builtin:
        return builtin
    user_path = config.chrome_path()
    if user_path and os.path.exists(user_path):
        return user_path
    if sys.platform == "darwin":
        return _mac_chrome_candidates()
    if os.name == "nt":
        return _first_existing(_win_chrome_candidates())
    return None


def _builtin_cft_candidates() -> str | None:
    """内置 Chrome for Testing 候选：打包态（.app 随包分发）优先，其次引擎数据目录
    旁的 CFT 副本（开发态）。R-6.11 引擎抽象层：CFT 独立身份
    （com.google.chrome.for.testing）不占用用户日常 Chrome 的 LaunchServices 单实例。
    注意 PyInstaller BUNDLE 布局差异：sys._MEIPASS = EXE 目录（Contents/MacOS），
    而 datas 落在 Contents/Resources——故两种布局都尝试（_MEIPASS/engine 与
    _MEIPASS.parent/Resources/engine）。"""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = pathlib.Path(meipass)
        candidates.append(
            str(mp / "engine/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        )
        candidates.append(
            str(mp.parent / "Resources/engine/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        )
    candidates.append(
        str(pathlib.Path.home()
            / "Library/Application Support/xhs-leads/engine/chrome-mac-x64"
            / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
    )
    for c in candidates:
        if c and pathlib.Path(c).exists():
            return c
    return None


def _mac_chrome_candidates() -> str | None:
    """macOS Chrome 定位候选：/Applications 默认安装 + ~/Applications 用户级安装。"""
    candidates = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        str(pathlib.Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    )
    return _first_existing(list(candidates))


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def _win_chrome_candidates() -> list[str]:
    """Windows Chrome 定位候选（最佳实践顺序）：注册表 → 常见安装路径。"""
    paths: list[str] = []
    try:
        import winreg
        for hive, sub in [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ]:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val:
                        paths.append(str(val))
            except OSError:  # noqa: PERF203
                continue
    except ImportError:
        pass
    paths += [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    return paths


def _profile_settings() -> tuple[int, str]:
    """CDP 端口与 default 账号 profile 目录（server 旧接口兼容；B 方案引擎不再使用）。"""
    return config.engine_port(), config.default_user_data_dir()


def chrome_running() -> bool:
    """检测 Chrome 是否在运行（跨平台，引擎池模式下基本不再使用，保留兼容）。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                                 capture_output=True, text=True, timeout=10).stdout
            return "chrome.exe" in out
        return subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def quit_chrome() -> bool:
    """退出 Chrome（兼容旧接口；引擎池优雅关闭走 engine_pool.close_all）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/IM", "chrome.exe", "/F"],
                           capture_output=True, timeout=10)
            return True
        r = subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to quit'],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ===========================================================================
# 页面加载与提取
# ===========================================================================
async def _load(url: str, wait_ms: int = 5000, account_id: str = "default"):
    page = await _page(account_id)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        try:
            await page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        await page.evaluate(_EXTRACT_SRC)
        await asyncio.sleep(wait_ms / 1000)
        return page
    except Exception:
        try:
            await _close_page_safe(page, account_id)
        except Exception:  # noqa: BLE001
            pass
        raise


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
        await ctx.add_init_script(_EXTRACT_SRC)
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
            await page.evaluate(_EXTRACT_SRC)
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
            await ctx.remove_init_script(_EXTRACT_SRC)
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
            await page.evaluate(_EXTRACT_SRC)
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
        text = await page.evaluate(
            "() => document.body ? document.body.innerText.slice(0, 1500) : ''"
        )
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
            n = await page.evaluate("() => document.querySelectorAll('section.note-item').length")
        except Exception:  # noqa: BLE001
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
    """登录墙分级处置（现象 3 机制核心：软风控不死等）。

    软风控（验证码/扫码/登录墙）→ mark_manual：任务中止但账号不熔断，
    页面保留供用户扫码/验证，登录成功后 server 自动 resolve，下个任务直接继续。
    硬风控（明确拦截）→ mark_hard：24h 熔断，绝不自动重试。
    """
    if any(k in wall for k in ("验证码", "安全验证", "扫码登录", "登录后查看", "请先登录")):
        risk.mark_manual(account_id, reason=wall)
    else:
        risk.mark_hard(account_id, reason=wall)


async def _prepare_search_page(keyword: str, filters: dict | None = None,
                               account_id: str = "default"):
    """打开搜索页并等卡片渲染；新版 AI 搜索页（无 note-item）回退旧版 URL。返回可用 page。

    检测登录墙/风控验证：Cookie 在但页面要求登录或验证时，立即抛
    BrowserLoginRequiredError（不空等、不空跑），由采集链路中止任务并提示人工处理。
    **登录墙页面保留不关闭**：用户需要在这个页面上扫码/验证恢复。
    """
    url = _XHS + "/search_result?keyword=" + _urlencode(keyword) + "&source=web_search_result_notes"
    page = await _load(url, 2500, account_id)
    appeared, wall = await _wait_note_cards(page, timeout_s=45)
    if wall:
        _handle_login_wall(account_id, wall)  # 软→引导扫码恢复；硬→熔断
        raise BrowserLoginRequiredError(wall)  # 保留页面供扫码，不关闭
    if not appeared:
        await _close_page_safe(page, account_id)
        page = await _load(_XHS + "/search_result?keyword=" + _urlencode(keyword), 2500, account_id)
        appeared, wall = await _wait_note_cards(page, timeout_s=45)
        if wall:
            _handle_login_wall(account_id, wall)
            raise BrowserLoginRequiredError(wall)
    if appeared:
        await _apply_filters(page, filters or {})
        if filters:
            _, wall = await _wait_note_cards(page, timeout_s=30)
            if wall:
                _handle_login_wall(account_id, wall)
                raise BrowserLoginRequiredError(wall)
    return page


async def _drain_page(page, items: list, seen: set) -> int:
    """提取当前页可见卡片并入 items（广告卡过滤 + 去重）。返回新增条数。"""
    await page.evaluate(_EXTRACT_SRC)  # 每次提取前重新注入（幂等）
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
    """
    page = await _prepare_search_page(keyword, filters, account_id)
    items: list = []
    seen: set = set()
    stale = 0
    deadline = time.time() + 120
    try:
        while time.time() < deadline:
            added = await _drain_page(page, items, seen)
            if on_progress:
                on_progress(len(items))
            done, stale = _should_stop_collect(len(items), limit, stale, added, should_stop)
            if done:
                break
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
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


async def open_front(account_id: str = "default") -> dict:
    """打开小红书首页前台标签（引导扫码）；复用已有 explore 标签避免堆积。"""
    page = await _page(account_id)
    try:
        await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
        await page.bring_to_front()
        return {"opened": True, "url": page.url[:60]}
    finally:
        pass  # 前台标签保留给用户扫码


async def launch_profile_chrome() -> dict:
    """（B 方案兼容）打开账号引擎的小红书登录页供扫码。

    引擎由 engine_pool 自动管理（launch_persistent_context），不再需要
    「带 --remote-debugging-port 启动」；本函数保留原名供旧 UI 调用。
    """
    return await open_front()


async def open_inspect_remote_debugging() -> dict:
    """（B 方案兼容）打开账号引擎的小红书登录页供扫码。

    引擎自动管理调试能力，无需用户开启 chrome://inspect 远程调试设置。
    """
    return await open_front()


async def inject_saved_cookies_now(account_id: str = "default") -> dict:
    """把本地已保存 Cookie 注入账号引擎（幂等；恢复链② 调用）。返回注入条数。"""
    saved = _saved_cookies(account_id)
    if not saved:
        return {"ok": False, "count": 0, "note": "本地无已保存 Cookie"}
    eng = await _engine(account_id)
    ok = await eng.inject_cookies(saved)
    return {"ok": ok, "count": len(saved)}


# ===========================================================================
# 引擎生命周期（server shutdown / 一次性动作）
# ===========================================================================
async def stop_profile():
    """（兼容旧接口）关闭 default 账号引擎：优雅 ctx.close() 落盘登录态。"""
    return await pool.close("default")


async def stop():
    """关闭全部引擎 + Playwright 实例（后端退出钩子）。"""
    await pool.close_all()


def _urlencode(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)
