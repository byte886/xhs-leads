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
import os
import random
import re
import time

from src import config, injections, logger as xlog, risk
from src.cdp_timeouts import (CdpTimeoutError, CDP_DRAIN, CDP_EVAL, CDP_NAV,
                              CDP_PREP, CDP_QUICK, cdp_call)
from src.engine_pool import pool

log = xlog.get("browser")

_ROOT = config.resource_root()  # 跨平台资源根（mac Resources / Windows onedir 同目录）
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
    """判定 Playwright 页面/渲染崩溃类异常（Page crashed / Target closed）。

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
    """停止后恢复采集窗口——清理残留采集页（搜索/笔记/探索页），
    引擎不再有采集页在导航/滚动（CDP 行为完全停止）。登录墙页保留。"""
    return await _cleanup_stale_xhs_pages(account_id)


def _is_purgeable_xhs_page(url: str) -> bool:
    """残留页判据：只清**搜索结果页 / 详情页 / 作者页**这类采集过程页。

    +（Nov 6 实测）：原判据把 explore 首页也当残留，采集窗正停在该页时被
    一起关掉 → keeper 判定「右窗被关闭」并重建 → 采集任务丢页直接终止。explore 首页
    与登录页是采集窗的正常驻留页，必须保留；登录墙 / 验证码页同样保留。
    """
    if "xiaohongshu.com" not in url:
        return False
    if "/website-login" in url or "captcha" in url or "/login" in url:
        return False
    return "search_result" in url or "/explore/" in url or "/user/" in url


async def _close_purgeable_pages(pages, keep=None) -> int:
    """逐页关闭可清理页并计数（关闭失败/采集窗页静默跳过）。

    **采集调试窗页不算残留**。2026-11-09 真机复现「开始采集→停止→立刻再开始」
    后右窗变黑（about:blank）：新一轮的清理把上一轮留下的 search_result / 详情页关了，
    而采集窗里就那一页，关完窗口空了。

    keep：协调器登记的采集窗任务页，引用判活（标题守护会与账号标题互覆盖，不作主判据）。
    """
    closed = 0
    for p in pages:
        if keep is not None and p is keep:
            continue
        if not _is_purgeable_xhs_page(p.url or ""):
            continue
        try:
            await p.close()
            closed += 1
        except Exception:  # noqa: BLE001
            pass
    return closed


def _collect_window_ref():
    """协调器登记的采集窗任务页（shell 未就绪/未开窗返回 None）。"""
    try:
        from src import shell_window  # noqa: PLC0415

        return shell_window.current_collect_page()
    except Exception:  # noqa: BLE001
        return None


async def _cleanup_stale_xhs_pages(account_id: str = "default") -> int:
    """采集前清理残留的小红书搜索/探索页（登录墙页保留）。

    取证（Nov 5）：历史采集崩溃/中断后残留 search_result 页会僵死（CDP WS 无响应）
    且 cleanup_tabs 按设计保留所有 xiaohongshu.com tab → 页面累积放大引擎负担，
    与崩溃相互促进。每次采集前清掉非登录墙的旧 xhs 页，降低累积触发面。
    """
    try:
        eng = await _engine(account_id)
        ctx = await eng.context()
        closed = await _close_purgeable_pages(list(ctx.pages), _collect_window_ref())
        if closed:
            log.info("采集前清理残留 xhs 页 %d 个", closed)
        await _ensure_window_not_empty(ctx)
        return closed
    except Exception:  # noqa: BLE001
        return 0


async def _ensure_window_not_empty(ctx) -> bool:
    """清理后窗口一页不剩时补一个 explore 页，绝不把窗口留在 about:blank。

    空窗对用户就是一个黑窗口，既不可用也不像「在跑」，与「存在的窗口总是有用的」冲突。
    """
    if list(ctx.pages):
        return False
    try:
        await ctx.new_page()
        page = list(ctx.pages)[-1]
        await cdp_call(page.goto(f"{_XHS}/explore", wait_until="domcontentloaded"),
                       timeout=CDP_NAV, label="empty.window.restore", skip=True)
        log.info("清理后窗口已空，补开 explore 页 url=%s", page.url)
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("补开 explore 页失败 %s", str(exc)[:80])
        return False

# ===========================================================================
# 页面加载与提取
# ===========================================================================
async def _load_focus(page, show_front: bool, tag: str = "load") -> None:
    """焦点处理：前台可见 or 后台切回壳窗口 Tab1。失败非阻断。

    CFT 158 在后台 tab 上 Page.bringToFront 会永久挂起（CDP 假死直接
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

    统一 CDP 超时保护——引擎假死时 Playwright 内部 timeout 不触发
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
    """崩溃重试：重开页面再试一次；仍失败则清理并抛第二次异常。

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
    """打开页面并注入提取脚本（失败按崩溃重试一次）。

    show_front=False：采集后台完成——不 bring_to_front 抢前台，
    采集 tab 留在壳窗口 采集窗（设置页开关控制是否显示）。
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
    """定位壳窗口 Tab1（base 前缀、非当前页）。"""
    for p in page.context.pages:
        if not p.is_closed() and p != page and base and (p.url or "").startswith(base):
            return p
    return None


async def _switch_back_to_shell(page) -> None:
    """后台采集：窗口焦点回到壳窗口 Tab1（base_url 前缀 tab），采集 tab 保持存在。

    注：Chrome 单窗口仅一个 active tab——切换后采集 tab 为后台，若实测渲染节流
    导致采集 0 条，兜底由 collect_tab_visible=True（显示采集）承担。
    """
    try:
        base = (os.environ.get("XHS_SHELL_URL") or "").rstrip("/")
        shell = _find_shell_tab(page, base)
        if shell is not None:
            # 直接触发点，Nov 5 DIAG 取证 goto 0.5s 后卡死）——cdp_call 绝对超时保护，
            # 焦点切换非关键路径，超时跳过。
            await cdp_call(shell.bring_to_front(), timeout=CDP_QUICK,
                           label="switch_back_to_shell", skip=True)
    except Exception:  # noqa: BLE001
        pass


# ===========================================================================
# 登录墙 / 筛选 / 搜索
# ===========================================================================
def _wall_by_url(url: str) -> str:
    """按页面 URL 判定拦截原因（纯函数，便于单测与控圈复杂度）。

    平台人机验证页实测不带 `/login`（地址是
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
            timeout=CDP_EVAL, label="login_wall.text", skip=True)
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


_FILTER_LABELS = (
    ("排序依据", "sort"),
    ("笔记类型", "note_type"),
    ("发布时间", "time_range"),
    ("搜索范围", "scope"),
    ("位置距离", "distance"),
)
_FILTER_DEFAULTS = ("综合", "全部", "不限", "")


def _wanted_filters(filters: dict) -> list[tuple[str, str]]:
    """按固定顺序取出要点击的筛选项，跳过默认值。"""
    return [
        (label, filters.get(key))
        for label, key in _FILTER_LABELS
        if filters.get(key) and filters.get(key) not in _FILTER_DEFAULTS
    ]


async def _open_filter_panel(page) -> None:
    """展开筛选面板。

    （2026-11-09 真机回归）：把这里的展开动作从「JS 直接
    `div.filter.click()`」改成「Python 发鼠标事件」，结果 `search_result_ai`
    页上的「筛选」对程序化鼠标事件完全不响应（同坐标、同一手法，两天前那条
    JS click 通道每次都能展开：tags 0→35、可见选项 19 个、点完「图文」后
    active 由 综合/综合/不限… 变为 综合/综合/图文/不限…）。这里改回 JS click；
    面板展开有约 1s 延迟，所以展开后轮询到可见项出现为止，不固定 sleep。
    """
    opened = await page.evaluate(_FILTER_OPEN_JS)
    if isinstance(opened, dict) and not opened.get("open"):
        await page.evaluate(_FILTER_OPEN_CLICK_JS)
    vis = 0
    for _ in range(12):
        vis = await page.evaluate(_VIS_TAGS_JS)
        if isinstance(vis, int) and vis >= 5:
            break
        await asyncio.sleep(0.4)
    log.debug("筛选面板可见项=%s", vis)


async def _pick_filter_point(page, val: str):
    """轮询等选项渲染出来，再等平滑滚动停稳后重取一次坐标。

    坐标必须落在视口内：页面停在列表底部、或面板残留着 opacity>0 但已被裁掉的标签时，
    取到的 y 会远超 innerHeight，鼠标事件落到视口外 —— 表现为「筛选没生效」却查不出
    原因（Windows 连续第二次采集出现过）。越界时先把页面滚回筛选栏再重取一次。
    """
    pt = None
    for _ in range(4):
        pt = await page.evaluate(_FILTER_TAG_POINT_JS, val)
        if pt:
            break
        await asyncio.sleep(0.8)
    if not pt:
        return None
    await asyncio.sleep(0.8)
    pt = await page.evaluate(_FILTER_TAG_POINT_JS, val) or pt
    vp = await page.evaluate(_VIEWPORT_JS)
    if not (0 <= pt["x"] <= vp["w"] and 0 <= pt["y"] <= vp["h"]):
        log.info("筛选选项 %s 坐标 (%s,%s) 超出视口 %sx%s，滚回筛选栏后重取",
                 val, pt["x"], pt["y"], vp["w"], vp["h"])
        await page.evaluate(_FILTER_SCROLL_BACK_JS)
        await asyncio.sleep(0.7)
        pt = await page.evaluate(_FILTER_TAG_POINT_JS, val)
        if not pt:
            return None
        vp = await page.evaluate(_VIEWPORT_JS)
        if not (0 <= pt["x"] <= vp["w"] and 0 <= pt["y"] <= vp["h"]):
            log.info("筛选选项 %s 仍超出视口 (%sx%s)，放弃该项", val, vp["w"], vp["h"])
            return None
    return pt


async def _close_filter_panel(page) -> None:
    """收起筛选面板，等结果重新加载。

    （2026-11-13 Windows 真机）：这一步原来按坐标 `mouse.click(收起)`。面板在点击
    选项后已经重渲染，收起按钮的位置和取坐标时不一致，坐标就落到下面的笔记卡片上，
    页面直接跳进作者主页（`/user/profile/...`），搜索结果列表没了 —— 采集返回 0 条，
    前端提示「已完成 · 未采集到数据」，日志里连「搜索结束」都不会出现。Mac 900 宽的
    窗口下面板下方是空白，所以一直没暴露。改为页面内点击（与展开同一条通道），
    按钮找不到时才退回坐标点击。
    """
    closed = await page.evaluate(_FILTER_CLOSE_CLICK_JS)
    if not closed:
        closer = await page.evaluate(_FILTER_CLOSE_JS)
        if isinstance(closer, dict) and closer.get("x") is not None:
            log.debug("筛选面板找不到收起按钮，退回坐标点击 (%s,%s)",
                      closer["x"], closer["y"])
            await page.mouse.click(closer["x"], closer["y"])
    await asyncio.sleep(1.2)
    url = _page_url_now(page)
    if "search_result" not in (url or ""):
        log.warning("收起筛选面板后页面已漂离搜索页：%s", url)


_LOCATION_FILTER_LABEL = "位置距离"

# 收起面板：页面内点击（与展开同一条通道）。坐标点击会落到下方笔记卡片上，
# 页面跳作者主页后搜索结果全丢（Windows 640 窄窗实测）。
_FILTER_CLOSE_CLICK_JS = """() => {
    const el = [...document.querySelectorAll('[data-hp-kind]')]
        .find(e => e.getAttribute('data-hp-kind') === 'xhs-filter-op-收起')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('收起');
        });
    if (!el) return false;
    el.click();
    return true;
}"""

_VIEWPORT_JS = "() => ({w: innerWidth, h: innerHeight})"

# 坐标越界时把筛选栏滚回视口中央（优先滚筛选按钮，退化到页面顶部）。
_FILTER_SCROLL_BACK_JS = """() => {
  const el = document.querySelector('div.filter')
    || [...document.querySelectorAll('div, span, button')].find(x => {
      const t = (x.innerText || '').trim();
      return t.length <= 4 && t.includes('筛选');
    });
  if (el) { el.scrollIntoView({block: 'center', inline: 'nearest'}); return 'filter'; }
  window.scrollTo(0, 0);
  return 'top';
}"""

# 坐标点击未命中时的兜底：页面内直接点标签（不依赖滚动位置与遮挡）。
_FILTER_TAG_CLICK_JS = """(val) => {
  const want = 'xhs-filter-tag-' + val;
  let el = [...document.querySelectorAll('[data-hp-kind]')]
    .find(e => e.getAttribute('data-hp-kind') === want);
  if (!el) {
    el = [...document.querySelectorAll('div.tags')].find(x => {
      const t = ((x.innerText || '').trim().split(/\\s+/)[0] || '');
      return t === val && Number(getComputedStyle(x).opacity) > 0.01;
    });
  }
  if (!el) return false;
  el.click();
  return true;
}"""


def _filter_warn(label: str, val: str) -> dict:
    """未生效的筛选项 → 给前端看的人话提示。

    「位置距离」整组（同城 / 附近）依赖浏览器定位，失败原因单一且用户可自助排查，
    单独给一条可执行提示；其他组只说明「可能没被过滤」。
    """
    if label == _LOCATION_FILTER_LABEL:
        hint = (f"「{val}」没生效：浏览器没拿到定位坐标，结果仍是「不限」。"
                f"检查账号定位配置后重试。")
    else:
        hint = f"筛选「{label}·{val}」没有生效，结果可能没有被过滤。"
    return {"label": label, "value": val, "hint": hint}


async def _active_contains(page, val: str) -> bool:
    """当前面板的 active 项里是否包含目标值。"""
    active = await page.evaluate(_ACTIVE_TAGS_JS)
    return any(val in str(a) for a in (active or []))


async def _filter_is_active(page, val: str) -> bool:
    """点击后校验选项是否真被平台选中。

    平台点选项后会自动收起筛选面板，固定等待后 `div.tags.active` 已随面板
    不可见，直接读会把「实际已生效」误判成未生效（真机实测：点击后面板可见
    项归零，但重开面板能读到该项 active、结果也已过滤）。先读一次（面板仍
    展开的情况），读不到就重新展开面板并轮询 active，避免平台渲染抖动偶发误报。
    """
    await asyncio.sleep(1.0)
    if await _active_contains(page, val):
        return True
    first = await page.evaluate(_ACTIVE_TAGS_JS)
    log.info("筛选校验 %s：点击后 1s 未读到 active（当前 active=%s），重开面板复核", val, first)
    await _open_filter_panel(page)
    for _ in range(3):
        if await _active_contains(page, val):
            return True
        await asyncio.sleep(0.5)
    last = await page.evaluate(_ACTIVE_TAGS_JS)
    vis = await page.evaluate(_VIS_TAGS_JS)
    log.info("筛选校验 %s 判定未生效：复核 active=%s 可见项=%s", val, last, vis)
    return False


async def _apply_filters(page, filters: dict):
    """搜索页应用筛选（排序 / 笔记类型 / 发布时间 / 搜索范围 / 位置距离）。

    跳过默认值（综合 / 全部 / 不限）：只在用户明确选择非默认条件时点击对应选项。
    点击条件后页面会刷新，每项点击后用 `_filter_is_active` 校验是否真被选中。

    （2026-11-08 实测）：平台每个筛选选项有两层 `div.tags`——可见层，以及一层
    `data-hp-kind="xhs-filter-tag-<值>"` 的透明热区代理层。两层 innerText 都是
    「值\\n值」（可见层+代理层拼接），旧代码用 `.trim() === 值` 精确比较永远不命中，
    于是"选了图文/最新"实际一个筛选都没应用（用户看到 chips 变了、结果仍是视频）。
    现在优先按 data-hp-kind 精确锁定、用真鼠标点坐标；取不到坐标再退回首段文本比较。
    """
    wanted = _wanted_filters(filters)
    warns: list[dict] = []
    if not wanted:
        setattr(page, "_xhs_filter_warns", warns)
        return
    for label, val in wanted:
        await _open_filter_panel(page)
        pt = await _pick_filter_point(page, val)
        if pt:
            await page.mouse.click(pt["x"], pt["y"])
            if await _filter_is_active(page, val):
                log.info("筛选已选 %s=%s", label, val)
            else:
                # 兜底：坐标链路没命中（滚动位置/遮挡/坐标越界）时，改用页面内点击。
                # 这不是主路径，主路径仍是真鼠标事件；两种都失败才判未生效。
                await _open_filter_panel(page)
                hit = await page.evaluate(_FILTER_TAG_CLICK_JS, val)
                if hit and await _filter_is_active(page, val):
                    log.info("筛选已选 %s=%s（坐标点击未命中，页面内点击补上）", label, val)
                else:
                    log.warning("筛选未生效 %s=%s", label, val)
                    warns.append(_filter_warn(label, val))
        else:
            log.info("筛选选项未找到 %s=%s，跳过该项", label, val)
            warns.append(_filter_warn(label, val))
        await asyncio.sleep(1.5)
    setattr(page, "_xhs_filter_warns", warns)
    await _close_filter_panel(page)


# 「筛选」是开关：面板已展开时再点会收起，导致后面找不到选项。
# 判据直接看「可见选项是否已渲染」（>5 个即已展开）——锚点在页面重渲染后会消失，不可靠。
# 按钮文案在有筛选条件时是「已筛选」，所以按 div.filter 类定位（2026-11-08 实测），
# 再退回「文本含筛选且长度≤4」的兜底（注意别匹配到"筛选条件"这类大容器）。
_FILTER_OPEN_JS = """() => {
    const vis = [...document.querySelectorAll('div.tags')]
        .filter(e => Number(getComputedStyle(e).opacity) > 0.01).length;
    if (vis > 5) return {open: true};
    const el = document.querySelector('div.filter')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('筛选');
        });
    if (!el) return null;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {open: false, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""

_FILTER_OPEN_CLICK_JS = """() => {
    const el = document.querySelector('div.filter')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('筛选');
        });
    if (!el) return false;
    el.click();
    return true;
}"""

# 已展开的筛选选项数：可见份（opacity > 0.01）。未展开 0，展开后约 35。
_VIS_TAGS_JS = """() => [...document.querySelectorAll('div.tags')]
    .filter(e => Number(getComputedStyle(e).opacity) > 0.01).length"""

# 筛选面板里已被选中的项（active 类），用于验证点击是否真落到选项上。
_ACTIVE_TAGS_JS = """() => [...document.querySelectorAll('div.tags.active')]
    .map(e => ((e.innerText || '').trim().split('\\n')[0] || '').trim())
    .filter(Boolean)"""

_FILTER_CLOSE_JS = """() => {
    const el = [...document.querySelectorAll('[data-hp-kind]')]
        .find(e => e.getAttribute('data-hp-kind') === 'xhs-filter-op-收起')
        || [...document.querySelectorAll('div, span, button')].find(x => {
            const t = (x.innerText || '').trim();
            return t.length <= 4 && t.includes('收起');
        });
    if (!el) return null;
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""

# 按 data-hp-kind 锚点取选项坐标（属性值精确比较，不做选择器拼接，免注入/转义问题）；
# 找不到锚点时退回「首段文本等于目标值」的可见 tags 元素。
_FILTER_TAG_POINT_JS = """(val) => {
  const want = 'xhs-filter-tag-' + val;
  let el = [...document.querySelectorAll('[data-hp-kind]')]
    .find(e => e.getAttribute('data-hp-kind') === want);
  if (!el) {
    el = [...document.querySelectorAll('div.tags')].find(x => {
      const t = ((x.innerText || '').trim().split(/\\s+/)[0] || '');
      return t === val && Number(getComputedStyle(x).opacity) > 0.01;
    });
  }
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""


def _handle_login_wall(account_id: str, wall: str) -> None:
    """登录墙处置。

    历史背景（已修复 `e2ec0cc`，2026-11-06 复核本函数只调 mark_manual）：    拍板移除风控熔断后，曾有 24h 硬熔断（mark_hard→cooldown）残留在登录墙路径，
    导致 _require_login 403「请 X 小时后再试」把账号锁死。
    现统一 mark_manual：任务中止但账号不熔断，页面保留供扫码/验证；
    登录成功后 login_status 自动 resolve，下个任务直接继续。
    """
    risk.mark_manual(account_id, reason=wall)


def _check_login_wall(account_id: str, wall: str) -> None:
    """登录墙/风控验证命中即中止：软风控标记 + 抛错（页面保留供扫码）。"""
    if not wall:
        return
    _handle_login_wall(account_id, wall)
    raise BrowserLoginRequiredError(wall)  # 保留页面供扫码，不关闭


LOGIN_URL = "https://www.xiaohongshu.com/login"

# 刷新页面——实测首页刷新/直连搜索页 URL 更容易被要求登录或弹风控。搜索、进详情、
# 进作者页一律模拟真人操作（点搜索框 → 逐字键入 → 回车 / 点卡片）。登录态不确定时
# 走登录页间接进入：登录页在已登录情况下会自己跳到 explore，我们不插手。
# 实测 rect≈245,79,580,24）；旧的 #search-input 是隐藏元素（rect 全 0），点它等于点 (0,0)
# ——这是历史「点搜索框没反应」的代码层原因。按候选顺序取**有尺寸**的那个。
_SEARCH_BOX_JS = """() => {
  // #search-input-ai / textarea.textarea 是**折叠态点开后的搜索浮层**里的真实输入框
  // （Win11 640 宽实测：点 .search-icon 后浮层出现 522×24 的 textarea，id=search-input-ai）
  const cands = ['#search-input-ai', '#search-input-in-feeds', 'textarea.search-input',
                 '#search-input', 'input.search-input', 'textarea.textarea'];
  for (const sel of cands) {
    // 同一选择器可能命中多个（宽/窄两种头部布局各有一份），必须逐个量尺寸：
    // 只取 querySelector 第一个会把 0×0 的隐藏副本当成「没有搜索框」。
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) continue;
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }
  }
  // 兜底：任何可见的 input/textarea（浮层换 DOM 时不至于直接失效）
  for (const el of document.querySelectorAll('input, textarea')) {
    const r = el.getBoundingClientRect();
    if (r.width < 60 || r.height < 10) continue;
    if (el.readOnly || el.disabled) continue;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
  }
  return null;
}"""

# 窄窗口折叠态：窗口内宽 < ~720 时小红书把整条搜索区 display:none，只留一个 40×40
# 放大镜按钮（实测 Win11 采集窗 640 宽就是这么折叠的，focus() 永远拿不到焦点）。
# 这里点开它，让 Python 侧稍后重试聚焦。
_EXPAND_SEARCH_JS = """() => {
  const cands = ['#search-button', 'a.search-icon', 'button.search-icon', '.search-icon',
                 'header .search-icon', 'div.search-icon', '.search-area .icon'];
  for (const sel of cands) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) continue;
      el.click();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }
  }
  return null;
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
        if "captcha" in url or "website-login" in url:
            # 平台把 /login 直接跳人机验证时会一直停在这里，等满 30s 再报「限流」
            # 会让用户以为是自己操作问题。立即结束并说清要去做什么。
            log.warning("登录页停在人机验证：%s（需人工在采集窗完成验证后重试）",
                        url[:120])
            return False
        if "xiaohongshu.com" in url and "/login" not in url and "captcha" not in url:
            return True
    return False


# 保证「后端 login_status」与「采集侧未登录拦截」不会口径分裂）。
NEEDS_LOGIN_JS = _NEEDS_LOGIN_JS


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


async def _session_cookie_alive(page) -> bool:
    """会话 cookie（web_session）还在不在。

    实测盲区：被踢下线 / cookie 失效后，页面 DOM 仍停在登录态渲染（旧画面
    没刷新），只看渲染态会判成「已登录」，采集继续空跑到结束、最后当成「平台数据不足」
    正常收尾——正好与「需要登录就立刻结束本次采集」相反。故再核一次会话 cookie。
    读不到 cookie 时返回 True（不误杀，交给渲染态与后续请求兜底）。
    """
    try:
        cookies = await cdp_call(page.context.cookies(), timeout=CDP_EVAL,
                                 label="login.session_cookie", skip=True)
    except Exception:  # noqa: BLE001
        return True
    return any(c.get("name") == "web_session" and c.get("value") for c in (cookies or []))


def _is_browse_host(url: str) -> bool:
    """是否在「浏览站」（www.xiaohongshu.com/web 首页域）内。

    只认浏览站：creator.xiaohongshu.com（创作服务平台）与其它子域虽然有同一批
    Cookie，却是完全不同的信息架构——没有首页搜索框、没有侧边栏，落在那里就
    搜不了。旧判定用「'xiaohongshu.com' in url」把它们当浏览页放过，采集会在
    创作平台页上找搜索框，必然落空。
    """
    if not url:
        return False
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    except Exception:  # noqa: BLE001
        return False
    return host in ("www.xiaohongshu.com", "xiaohongshu.com")


async def _ensure_browse_page(page, account_id: str) -> bool:
    """确保页面处于登录态浏览页；不确定就走 /login（唯一允许 URL 的页面）。"""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    on_browse = _is_browse_host(url) and "/login" not in url and "captcha" not in url
    if on_browse and not await _page_needs_login(page) and await _session_cookie_alive(page):
        return True
    return await _open_login_page(page)


async def _preflight_browse(page, account_id: str) -> bool:
    """采集前置自检：域 → 登录 → 首页位置，逐项纠偏，任一不满足就修到满足再返回。

    不再假定「任务页一定停在首页且已登录」（实测：用户点过私信、创作或发布入口后，
    采集窗那一个标签就停在 /chat 或创作平台域，复用为任务页时旧逻辑一路放过，
    最后报「未采集到数据」而窗口明明登录着）。四步：

    1. 域：不在浏览站 → 走 /login（唯一允许 URL 的入口，平台自己跳 explore）
    2. 登录：渲染态 + web_session 双判定 → 未登录/被踢 → 走 /login，仍不行抛登录引导
    3. 位置：不在首页 → 点左上 logo 回首页（不用 URL、不刷新，防风控）
    4. 确认：回到首页后搜索框必须可聚焦，否则再走一次 /login 兜底

    返回 True 表示「可以开始搜索」。抛 BrowserLoginRequiredError 表示需要人工登录/验证。
    """
    if not await _ensure_browse_page(page, account_id):
        raise BrowserLoginRequiredError(
            "小红书要求登录（请在采集窗完成扫码/人机验证后重试）")
    if not await _back_to_feeds(page):
        # 页面既没 logo 可点也回不了首页（例如被钉在某个子域）：走 /login 兜底跳转
        if not await _open_login_page(page):
            raise BrowserLoginRequiredError(
                "页面无法回到小红书首页（请在采集窗手动回到首页后重试）")
        if not await _back_to_feeds(page):
            raise BrowserLoginRequiredError(
                "页面无法回到小红书首页（请在采集窗手动回到首页后重试）")
    log.info("前置自检通过：url=%s", _page_url_now(page))
    return True


async def _search_by_typing(page, keyword: str, account_id: str) -> bool:
    """模拟真人搜索：聚焦搜索框 → 清空残留 → 逐字输入 → 回车 → 核对真的进了结果页。

    （实机复现）：坐标点击搜索框会让焦点落在 BODY——`elementFromPoint(535,72)`
    明明是 TEXTAREA（#search-input-in-feeds），点完 activeElement 却是 BODY，输入
    的文字一个都不进框、回车无效，页面停在 explore。旧代码只看「有没有卡片」，
    而 explore 首页本来就有 30 张推荐卡，于是把推荐流当成关键词结果入库
    （用户看到的就是「压根没有采集过程，还混进视频」）。
    改法：用 JS focus 直接聚焦（实测可靠），聚焦后清空残留再输入。
    """
    focused = await cdp_call(page.evaluate(_FOCUS_SEARCH_BOX_JS), timeout=CDP_EVAL,
                             label="search.focus")
    if not focused:
        # 窄窗口（实测 Win11 采集窗 640 宽）下小红书把搜索区折叠成放大镜按钮：
        # 先点开它，再重试聚焦。这是 Windows 上「聚焦=False→0 条」的真因。
        trig = await cdp_call(page.evaluate(_EXPAND_SEARCH_JS), timeout=CDP_EVAL,
                              label="search.expand", skip=True)
        if trig:
            await asyncio.sleep(1.2)
            focused = await cdp_call(page.evaluate(_FOCUS_SEARCH_BOX_JS), timeout=CDP_EVAL,
                                     label="search.focus2")
    if not focused:
        # 退路：按坐标点一次（同一选择器逐个量尺寸后的可见框）
        box = await cdp_call(page.evaluate(_SEARCH_BOX_JS), timeout=CDP_EVAL,
                             label="search.box", skip=True)
        if box:
            await cdp_call(page.mouse.click(box["x"], box["y"]), timeout=CDP_QUICK,
                           label="search.click")
            await asyncio.sleep(0.8)
            focused = await cdp_call(page.evaluate(_FOCUS_SEARCH_BOX_JS), timeout=CDP_EVAL,
                                     label="search.focus3")
    log.info("搜索聚焦=%s url=%s", focused, _page_url_now(page))
    if not focused:
        # 取不到焦点就别硬输入：盲打+回车会停在 explore，把推荐流误当结果（历史 bug）。
        # 这里明确失败并带上视口宽度，方便判断是不是窗口太窄被折叠。
        active = await cdp_call(page.evaluate(_FOCUS_SELF_CHECK_JS), timeout=CDP_EVAL,
                                label="search.active", skip=True)
        log.warning("搜索框取不到焦点，跳过本轮（active=%s）", active)
        return False
    # 上一次采集可能残留关键词：全选后删除，避免拼成「翡翠翡翠」
    await cdp_call(page.evaluate(_SELECT_FOCUSED_TEXT_JS), timeout=CDP_EVAL, label="search.select")
    await cdp_call(page.keyboard.press("Backspace"), timeout=CDP_QUICK, label="search.clear")
    await cdp_call(page.keyboard.type(keyword, delay=90), timeout=CDP_EVAL, label="search.type")
    await asyncio.sleep(0.5)
    await cdp_call(page.keyboard.press("Enter"), timeout=CDP_QUICK, label="search.enter")
    arrived = await _wait_search_result_url(page)
    if not arrived:
        # 回车没反应（新版首页光标在文字区，回车只换行）：改点搜索按钮/联想首项
        sub = await cdp_call(page.evaluate(_SUBMIT_SEARCH_JS), timeout=CDP_EVAL,
                             label="search.submit", skip=True)
        if sub:
            log.info("回车未跳转，改用点击提交（%s）", sub)
            arrived = await _wait_search_result_url(page, timeout_s=12)
    if not arrived:
        log.warning("搜索未进入结果页（停在 %s），判定失败、不采集", _page_url_now(page))
        return False
    appeared, wall = await _wait_note_cards(page, timeout_s=20)
    log.info("搜索回车后 url=%s cards_ok=%s wall=%s", _page_url_now(page), appeared, wall or "-")
    _check_login_wall(account_id, wall)
    return appeared


async def _wait_search_result_url(page, timeout_s: float = 10) -> bool:
    """等 URL 真的进到 search_result 再判定，别回车后一锤子查。

    （Nov 9 实测）：回车后小红书 SPA 路由要 0.3~1s 才换 URL，而
    `_wait_note_cards` 在 explore 首页立刻就能数到推荐卡片（首页本来就有）、于是
    立即返回，紧接着查 URL 时仍是 explore → 把已经搜成功的这一轮判成失败。用户侧
    看到的就是「右窗明明已经进了搜索结果页，左窗却说未采集到数据」。故这里轮询 URL。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await _is_on_search_result(page):
            return True
        await asyncio.sleep(0.25)
    return False


async def _is_on_search_result(page) -> bool:
    """是否真的进了搜索结果页。

    +（Nov 6 实测）：搜索没生效时 explore 首页本来就有 30 张推荐卡片，
    只看「有卡片」会误判成功，把推荐流当成关键词结果入库（实测采到与关键词无关的
    笔记）。必须再核对 URL 已进入 search_result。
    """
    try:
        return "search_result" in (page.url or "")
    except Exception:  # noqa: BLE001
        return False


_FOCUS_SEARCH_BOX_JS = """() => {
  const cands = ['#search-input-ai', '#search-input-in-feeds', 'textarea.search-input',
                 '#search-input', 'input.search-input', 'textarea.textarea'];
  for (const sel of cands) {
    // 逐个量尺寸：同一选择器可能命中隐藏副本（宽/窄头部各一份），首个往往是 0×0
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) continue;
      el.focus();
      // activeElement === el 之外还接受「焦点落在它的子元素」（部分实现会移到内部 input）
      if (document.activeElement === el || el.contains(document.activeElement)) return true;
    }
  }
  // 兜底：可见的 input/textarea（搜索浮层换 DOM 时仍能聚焦）
  for (const el of document.querySelectorAll('input, textarea')) {
    const r = el.getBoundingClientRect();
    if (r.width < 60 || r.height < 10) continue;
    if (el.readOnly || el.disabled) continue;
    el.focus();
    if (document.activeElement === el || el.contains(document.activeElement)) return true;
  }
  return false;
}"""

_FOCUS_SELF_CHECK_JS = """() => {
  const el = document.activeElement;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {tag: el.tagName, id: el.id || '', w: Math.round(r.width), h: Math.round(r.height),
          viewport: window.innerWidth};
}"""

_SUBMIT_SEARCH_JS = """() => {
  // 新版首页本例：光标在文字区里回车不再跳结果页（实测停在 /explore，键入的
  // 关键词只弹出联想面板）。回车无效时按真人做法点「搜索」按钮或联想首项。
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 10 && r.height > 10;
  };
  const btnSels = ['#search-button', '.search-button', 'button.search-btn',
                   '.search-icon', 'div.search-icon', '[class*="search-icon"]'];
  for (const sel of btnSels) {
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue;
      el.click();
      return {how: 'button:' + sel};
    }
  }
  const sug = document.querySelector('.search-suggest-list, .suggest-list, [class*="suggest"]');
  if (sug) {
    const first = sug.querySelector('li, [class*="item"]');
    if (first) { first.click(); return {how: 'suggest'}; }
  }
  const txtBtn = [...document.querySelectorAll('button, div, span')]
    .find((b) => visible(b) && (b.innerText || '').trim() === '搜索');
  if (txtBtn) { txtBtn.click(); return {how: 'text-搜索'}; }
  return null;
}"""

_SELECT_FOCUSED_TEXT_JS = """() => {
  const el = document.activeElement;
  if (el && typeof el.select === 'function') { el.select(); return true; }
  return false;
}"""

_LOGO_JS = """() => {
  const el = document.querySelector('a.side-bar-logo-wrapper') || document.querySelector('.header-logo');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 10 || r.height < 10) return null;
  return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}"""


def _is_on_feeds(url: str) -> bool:
    """是否已在首页（/explore 且不是结果页）。"""
    return "xiaohongshu.com/explore" in url and "search_result" not in url


async def _click_logo_back(page) -> bool:
    """点左上 logo 回首页；页面没有可点的 logo 时返回 False。"""
    pos = await cdp_call(page.evaluate(_LOGO_JS), timeout=CDP_EVAL, label="back.logo", skip=True)
    if not pos:
        return False
    await cdp_call(page.mouse.click(pos["x"], pos["y"]), timeout=CDP_QUICK, label="back.click")
    await asyncio.sleep(2.5)
    return True


async def _back_to_feeds(page) -> bool:
    """把页面带回首页（模拟点击左上 logo；不用 URL、不刷新）。

    （Nov 6 实测）：结果页顶部搜索区的位置与首页不同，同坐标命中的是
    DIV、点下去焦点落在 BODY，直接点框不会聚焦输入框。回首页后搜索入口才是稳定
    可点的 #search-input-in-feeds。

    （Nov 9 补）：只认 search_result 是不够的——采集任务页复用采集窗里那个标签，
    用户点过「消息/发布/个人页」后页面停在 /chat、/user/profile 等子页，旧逻辑
    直接判「已在首页」放过，接着在子页找搜索框必然落空，采集报「未采集到数据」
    而窗口明明是登录态。现在只要不在首页就点 logo 回首页。
    """
    if _is_on_feeds(_page_url_now(page)):
        return True
    if not await _click_logo_back(page):
        log.warning("回首页失败：当前页（%s）没有可点的 logo", _page_url_now(page))
        return False
    if _is_on_feeds(_page_url_now(page)):
        return True
    log.warning("点 logo 后仍未回到首页（停在 %s）", _page_url_now(page))
    return False


async def _prepare_search_page(keyword: str, filters: dict | None = None,
                               account_id: str = "default"):
    """进入搜索页并等卡片渲染——只靠页面内交互，不用 URL、不刷新。

    路径：取任务页 → 前置自检（域/登录/回首页逐项纠偏）→ 点搜索框输入关键词回车。
    未登录/停在登录页 → 抛 BrowserLoginRequiredError（页面保留供扫码/验证）。
    空结果重试一次（重新点框输入），仍空则按 0 条收尾，不再 reload。
    """
    page = await _page(account_id)
    if not await _preflight_browse(page, account_id):
        raise BrowserLoginRequiredError(
            "小红书要求登录（请在采集窗完成扫码/人机验证后重试）")
    appeared = await _search_by_typing(page, keyword, account_id)
    if not appeared:
        appeared = await _search_by_typing(page, keyword, account_id)
    if not appeared:
        # 类型也不受筛选约束（用户看到「没有采集过程却采到视频」就是这个）。
        # 宁可判失败，也不能把推荐流当关键词结果入库。
        log.warning("搜索未进入结果页（停在 %s），判定失败、不采集", _page_url_now(page))
        return None
    _remember_page(page, account_id)
    await _apply_filters(page, filters or {})
    if filters:
        _, wall = await _wait_note_cards(page, timeout_s=20)
        _check_login_wall(account_id, wall)
    return page


def _card_type_matches(it: dict, want_type: str | None) -> bool:
    """搜索卡片是否符合「图文/视频」筛选。

    平台筛选是靠点击页面上的筛选按钮实现的，点击失败或没生效时视频就会混进来。
    这里在提取层再兜一道：卡片字段是 extract.js 给的 type='video'/'note'，
    want_type 是前端中文值（图文/视频/全部），「全部」或空恒通过。
    """
    if not want_type or want_type == "全部":
        return True
    is_video = (it.get("type") or "").lower() == "video"
    if want_type == "视频":
        return is_video
    if want_type == "图文":
        return not is_video
    return True


async def _drain_page(page, items: list, seen: set, want_type: str | None = None) -> int:
    """提取当前页可见卡片并入 items（广告卡过滤 + 类型过滤 + 去重）。返回新增条数。"""
    await injections.inject_once(page, "extract")  # 注册表幂等注入：页面已注入则跳过
    got = await page.evaluate("() => xhsExtractSearchNotes()")
    added = 0
    for it in got:
        k = it.get("feed_id") or it.get("id")
        # 过滤推广/广告卡片：真实笔记 id 是 24 位 hex（推广位是 UUID 等其他格式）
        if not re.fullmatch(r"[0-9a-f]{24}", str(k or "")):
            continue
        if not _card_type_matches(it, want_type):
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
        if stale >= 2 and count > 0:
            return True, stale
    else:
        stale = 0
    return False, stale


async def _open_search_page(keyword: str, filters, account_id: str,
                            label: str = "prepare_search_page"):
    """准备搜索页：cdp_call 绝对超时保护。"""
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
    """采集页崩溃恢复：重开搜索页返回新 page；非崩溃/超限/重开失败返回 None。

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
                        should_stop, on_progress, want_type: str | None = None) -> tuple[int, int, bool]:
    """一轮：提取新卡片 → 播报进度 → 终止判定 → 滚动下一页。

    返回 (added, stale, done)。done=True 表示本轮后收尾（采满/用户停止/不再增长）。
    假死与崩溃异常直接上抛，由 `_search_loop` 统一处置。
    """
    added = await cdp_call(_drain_page(page, items, seen, want_type),
                           timeout=CDP_DRAIN, label="drain_page")
    if on_progress:
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
                page, items, seen, limit, stale, should_stop, on_progress,
                want_type=(filters or {}).get("note_type"))
        except CdpTimeoutError:
            return page, stale
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
                 filters: dict | None = None, account_id: str = "default",
                 keep_page: bool = False) -> dict:
    """任务式采集：滚动平台搜索页逐批提取，支持去重合并、进度回调与中途停止。

    - on_progress(n, items)：每批提取后回调已采集数量与 items 引用
    - should_stop()：返回 True 时提前收尾（用户点「停止采集」）
    - filters：搜索筛选（排序/笔记类型/发布时间/搜索范围/位置距离）
    - account_id：使用哪个账号引擎采集（默认 default）
    - keep_page：True 时保留搜索页并留在活动页登记，供随后逐条点开补详情；
      False（默认）结束即登记清除并关页。配套释放走 release_search_page()。
    - 总时长上限 120s：平台 AI 搜索页渲染慢时的兜底，超时返回已采集部分
    -渲染进程崩溃（Page crashed / Target closed）时自动重开搜索页继续，
      最多 2 次；已采 items 保留。
    """
    page = await _search_prepare(keyword, filters, account_id)
    if page is None:
        return {"keyword": keyword, "count": 0, "items": [],
                "stopped": bool(should_stop and should_stop()), "filter_warnings": []}
    items: list = []
    seen: set = set()
    try:
        page, _stale = await _search_loop(page, keyword, filters, account_id,
                                          items, seen, limit, 0, should_stop, on_progress)
        if page is not None:
            # 崩溃重开会换成新页：同步登记，否则详情阶段拿着已崩的旧页
            _remember_page(page, account_id)
        log.info("搜索结束 url=%s cards=%d", _page_url_now(page), len(items))
        return {"keyword": keyword, "count": len(items), "items": items,
                "stopped": bool(should_stop and should_stop()),
                # 没生效的筛选（尤其「位置距离」组）必须带出去，前端才能提示用户；
                # 静默失败是这一类问题最坑的地方（结果不对但没有任何反馈）。
                "filter_warnings": getattr(page, "_xhs_filter_warns", []) or []}
    finally:
        if not keep_page:
            _forget_page(account_id)
            if page is not None:
                await _close_page_safe(page, account_id)

# ===========================================================================
# 详情 / 作者 / 评论
# ===========================================================================
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
_ACTIVE_PAGES: dict[str, object] = {}

_CARD_SCROLL_JS = """(fid) => {
  const cards = [...document.querySelectorAll('section.note-item')];
  const hit = cards.find(c => {
    const a = c.querySelector('a');
    return a && ((a.getAttribute('href') || '') + '').indexOf(fid) >= 0;
  });
  if (!hit) return false;
  hit.scrollIntoView({block: 'center', inline: 'nearest'});
  return true;
}"""

_CARD_POINT_JS = """(fid) => {
  const cards = [...document.querySelectorAll('section.note-item')];
  const hit = cards.find(c => {
    const a = c.querySelector('a');
    return a && ((a.getAttribute('href') || '') + '').indexOf(fid) >= 0;
  });
  if (!hit) return null;
  const r = hit.getBoundingClientRect();
  if (r.width < 50 || r.height < 50) return null;
  const y = r.y + Math.min(100, r.height / 2);
  if (y < 90 || y > 750) return null;
  return {x: Math.round(r.x + r.width / 2), y: Math.round(y)};
}"""


def _remember_page(page, account_id: str = "default") -> None:
    """记下当前采集搜索页：详情/作者/评论只能在这个页面上点开，不新开 URL。"""
    _ACTIVE_PAGES[account_id] = page


def _forget_page(account_id: str = "default") -> None:
    _ACTIVE_PAGES.pop(account_id, None)


async def release_search_page(account_id: str = "default") -> None:
    """释放 search(keep_page=True) 留下的搜索页：清登记并关页（补全结束后调用）。"""
    page = _ACTIVE_PAGES.pop(account_id, None)
    if page is None:
        return
    try:
        await _close_page_safe(page, account_id)
    except Exception:  # noqa: BLE001 释放失败不影响任务收尾
        pass


def _page_url_now(page) -> str:
    """当前页 URL 摘要（诊断用，取不到返回 ?）。"""
    try:
        return str(page.url)[:90]
    except Exception:  # noqa: BLE001
        return "?"


def _active_page(account_id: str = "default"):
    """取活动搜索页；已关闭视为没有（调用方按缺数据降级，不回退到 URL）。"""
    page = _ACTIVE_PAGES.get(account_id)
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:  # noqa: BLE001
        return None
    return None


def _page_position(page) -> str:
    """给当前页贴位置标签（判断顺序敏感：首页 `/explore` 不含 `/explore/`）。"""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        return "other"
    if "website-login/error" in url or "error_code=300013" in url:
        return "error"          # 必须排在 /explore/ 之前：redirectPath 里带着 /explore/xx
    if "/user/profile/" in url:
        return "author"
    if "search_result" in url:
        return "search"
    if "/explore/" in url:
        return "detail"
    if "xiaohongshu.com/explore" in url:
        return "feeds"
    if "127.0.0.1" in url:
        return "app"
    return "other"


async def _close_stray_pages(page, before: set, account_id: str) -> int:
    """关掉采集过程中意外新开的小红书页（首页/作者页），防孤儿页堆积。"""
    closed = 0
    for p in list(page.context.pages):
        try:
            if id(p) in before or p is page or _page_position(p) not in ("detail", "author"):
                continue
            await _close_page_safe(p, account_id)
            closed += 1
        except Exception:  # noqa: BLE001
            continue
    return closed


async def _back_to_list(page) -> bool:
    """从详情页返回结果页：只在"确实站在详情页"时才后退，退完校验落点。

    +（Nov 6 修）：原来是无条件 `go_back()` + 等 1.5s —— 点卡片落空时
    会把搜索页直接退到 `/explore` 首页，后续所有详情都点不到卡片；平台多 push
    一条历史时又会退到中间态。改成：先断言当前位置是详情页，退一次，再校验是否
    落回搜索结果页，不落就如实打日志交给调用方降级（绝不连退）。
    """
    if _page_position(page) != "detail":
        log.info("返回跳过：当前不在详情页 url=%s", _page_url_now(page))
        return False
    await cdp_call(page.go_back(), timeout=CDP_NAV, label="back.history", skip=True)
    await asyncio.sleep(1.5)
    where = _page_position(page)
    if where != "search":
        log.warn("后退未落回搜索页 pos=%s url=%s", where, _page_url_now(page))
        return False
    return True


def _detail_url(feed_id: str, xsec_token: str) -> str:
    """详情页直开链接：token 取自采集时落地详情页的 URL（采集已入库）。"""
    return (f"https://www.xiaohongshu.com/explore/{feed_id}"
            f"?xsec_token={xsec_token}&xsec_source=pc_search")


# 详情页复用槽：account_id → 上一次采集用的详情页标签。
# （2026-11-13 实测）：风控点不在"页面内滚动"，而在"反复打开新页面"——同一篇笔记
# 短时间内被开了四五次独立 tab 后，页面被平台跳到 website-login/error。连续采多篇
# 时改成同一标签内切地址，只开一次页，行为特征接近真人翻页。
_DETAIL_PAGE: dict[str, object] = {}


_RELIMIT_MSG = ("平台判定本次访问异常（300013）已限制采集，已自动退出登录。"
                 "请在右侧窗口重新扫码登录，登录后重新点「采集客户」即可。")


async def _risk_relogin(page, account_id: str, note_id: str = ""):
    """被平台判异常访问（300013）时的唯一处置：退出登录 + 回登录页 → 抛登录要求。

    实测（2026-11-13）：换新标签 / 跳首页 / 在正常首页点卡片三条路径都照拦，
    只有「登出（清 cookie）→ 重新扫码登录」能立刻恢复，故这里只做这一条。
    """
    log.info("平台判定异常访问，退出登录并回登录页 note_id=%s", note_id)
    await _force_relogin(page, account_id)
    _handle_login_wall(account_id, "平台判定异常访问（300013），已退出登录")
    raise BrowserLoginRequiredError(_RELIMIT_MSG)


async def _open_detail_page(feed_id: str, xsec_token: str, account_id: str = "default"):
    """打开目标笔记详情页：优先复用上次那个标签，同页导航到新地址。

    （2026-11-08 实测）：用库里 xsec_token 拼回 feed_id 直开详情页可行——落地 URL
    未被重定向、`.note-scroller` 在、评论已渲染、无「当前笔记暂时无法浏览」。
    因此「采评论」不必再依赖采集留下的搜索页：开始采集与采集客户彻底解耦。
    """
    if not xsec_token:
        return None
    url = _detail_url(feed_id, xsec_token)

    page = _DETAIL_PAGE.get(account_id)
    if page is not None:
        try:
            if page.is_closed():
                page = None
        except Exception:  # noqa: BLE001
            page = None
        if page is not None:
            try:
                await cdp_call(page.goto(url, wait_until="domcontentloaded"),
                               timeout=CDP_NAV, label="detail.reuse", skip=True)
                await asyncio.sleep(5)
                pos = _page_position(page)
                if pos == "error":
                    await _risk_relogin(page, account_id, feed_id)
                if pos == "detail":
                    return page
                log.info("复用详情页未进详情 url=%s", _page_url_now(page))
            except Exception as exc:  # noqa: BLE001
                log.info("复用详情页失败 %s", str(exc)[:80])
            _DETAIL_PAGE.pop(account_id, None)

    try:
        page = await (await _ctx(account_id)).new_page()
    except Exception:  # noqa: BLE001
        return None
    try:
        await cdp_call(page.goto(url, wait_until="domcontentloaded"),
                       timeout=CDP_NAV, label="detail.direct", skip=True)
        await asyncio.sleep(5)
        pos = _page_position(page)
        if pos == "error":
            _DETAIL_PAGE[account_id] = page
            await _risk_relogin(page, account_id, feed_id)
        if pos == "detail":
            _DETAIL_PAGE[account_id] = page
            return page
        log.info("token 直开未进详情页 url=%s", _page_url_now(page))
    except Exception as exc:  # noqa: BLE001
        log.info("token 直开失败 %s", str(exc)[:80])
    await _close_page_safe(page, account_id)
    return None


async def _force_relogin(page, account_id: str = "default") -> None:
    """平台判异常访问（300013 / rejectUrl=comment/page）时，主动退出登录并停在登录页。

    （2026-11-13 用户实测）：登出 → 重新扫码登录后限制立刻解除；而换新标签、跳首页、
    在正常首页用真鼠标点卡片这三条路径全部无效。故这里直接清会话 cookie、回 /login。
    """
    try:
        page.context.clear_cookies()
        log.info("已清除会话 cookie（主动退出登录）account=%s", account_id)
    except Exception as exc:  # noqa: BLE001
        log.info("清除 cookie 失败 %s", str(exc)[:60])
    try:
        await cdp_call(page.goto(LOGIN_URL, wait_until="domcontentloaded"),
                       timeout=CDP_NAV, label="relogin", skip=True)
        await asyncio.sleep(2)
        log.info("已回到登录页等待重新扫码 url=%s", _page_url_now(page))
    except Exception as exc:  # noqa: BLE001
        log.info("回登录页失败 %s", str(exc)[:60])


async def _open_note_card(page, note_id: str) -> bool:
    """在搜索页点开目标卡片：先滚到视口内，再取坐标用真鼠标点击。

    +（Nov 6 实测）：卡片 <a href> 是裸链接（无 xsec_token），JS `a.click()`
    走裸链接会被平台判非法直接跳 /404?source=/404/sec_...；真鼠标点击时平台自己补
    xsec_token，正常进入详情页。
    """
    found = await cdp_call(page.evaluate(_CARD_SCROLL_JS, note_id),
                           timeout=CDP_EVAL, label="note.scroll", skip=True)
    if not found:
        return False
    await asyncio.sleep(0.4)
    pt = await cdp_call(page.evaluate(_CARD_POINT_JS, note_id),
                        timeout=CDP_EVAL, label="note.point", skip=True)
    if not pt:
        return False
    await cdp_call(page.mouse.click(pt["x"], pt["y"]), timeout=CDP_QUICK, label="note.click")
    return True


async def _read_note_detail(page) -> dict:
    """详情页提取：确认已进详情 → 重新注入提取脚本（导航后旧注入已失效）→ 取字段。"""
    if "/explore/" not in (page.url or ""):
        return {}  # 没跳进详情（广告位/点击落空），按缺数据降级
    await injections.inject_once(page, "extract")
    return await page.evaluate("() => xhsExtractNoteDetail()")


_AUTHOR_OPEN_JS = """(uid) => {
  // 详情页里有 60+ 个 /user/profile/ 链接（作者本人、评论区、推荐位、侧栏「我」），
  // 而且它们**统统被 VIDEO 遮罩或 note-detail-mask 盖住**——Nov 12 实测：按坐标点击，
  // 命中点上层是 VIDEO/遮罩而非链接本身，点了开不出页（3/3 失败）；旧实现靠「y40~400 带内
  // 第一个」还会点到推荐位里的隔壁博主，取回**别人的**粉丝数。
  // 改为按笔记作者 uid 精确匹配 href，并在 DOM 层直接触发该链接的点击：
  // 平台仍按真实点击处理、照常开新标签页，且天然绕开视觉遮挡。
  if (!uid) return {ok: false, reason: 'no-uid'};
  const links = [...document.querySelectorAll('a[href*="/user/profile/"]')]
    .filter(a => (a.getAttribute('href') || '').includes('/user/profile/' + uid));
  if (!links.length) return {ok: false, reason: 'not-found'};
  const a = links[0];
  const href = a.getAttribute('href') || '';
  a.click();
  return {ok: true, href, count: links.length};
}"""


async def _extract_user(target) -> dict:
    """在作者页上取资料（导航后需重新注入提取脚本）。"""
    await injections.inject_once(target, "extract")
    return await target.evaluate("() => xhsExtractUser()") or {}


def _find_new_user_page(page, before: set):
    """在当前 context 里找点击后新出现、且是作者主页的标签页。"""
    for p in page.context.pages:
        try:
            if id(p) not in before and "/user/profile/" in (p.url or ""):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


async def _wait_user_page(page, before: set, tries: int = 16):
    """等新开的作者页出现（最多 tries × 0.5s，实测 1~4s 出现）。"""
    for _ in range(tries):
        fresh = _find_new_user_page(page, before)
        if fresh is not None:
            return fresh
        await asyncio.sleep(0.5)
    return None


def _uid_from_profile_url(url: str) -> str:
    """从作者页 URL 取 uid（/user/profile/<uid>?xsec_token=...）。"""
    m = re.search(r"/user/profile/([0-9a-zA-Z]+)", url or "")
    return m.group(1) if m else ""


async def _open_profile_page(page, href: str):
    """兜底：新标签页直接打开作者页 URL（href 自带 xsec_token，实测与点击等效）。"""
    if not href:
        return None
    url = href if href.startswith("http") else "https://www.xiaohongshu.com" + href
    try:
        np = await page.context.new_page()
        await np.goto(url, wait_until="domcontentloaded", timeout=20000)
        return np
    except Exception:  # noqa: BLE001
        return None


async def _read_author_profile(page, uid: str = "", account_id: str = "default") -> dict:
    """详情页内点作者 → 平台新开一个作者页标签 → 在它上面取资料 → 立刻关掉。

    +（Nov 6 实测）：点作者**不是同页跳转，而是新标签页**（引擎里一度堆了
    3 个作者页），所以不能靠等当前页 URL 变化；拿到新页后取完立即关闭，搜索页与历史栈
    都不受影响。搜索页上点作者会污染历史栈，所以只能在这里（详情页内）做。

    +（Nov 12 实测，两条血案都出在这里）：
    1. 详情页有 68 个 /user/profile/ 链接，旧实现取「y40~400 带内第一个」，
       那个位置排着的是**推荐位/评论区的隔壁博主**，取回的是别人的粉丝数——比 0 更危险，
       因为 0 会被当未知降级，错值会被当真值打分。
    2. 这些链接**全被 VIDEO 遮罩盖住**，鼠标坐标点击落到遮罩上，页根本开不出来（3/3 失败）。
       所以改成按作者 uid 精确匹配 href + DOM 层直接触发点击；仍打不开时按 href 直开兜底。
    点开/直开后一律校验新页 uid，与目标不符直接丢弃。
    """
    before = {id(p) for p in page.context.pages}
    res = await cdp_call(page.evaluate(_AUTHOR_OPEN_JS, uid),
                         timeout=CDP_EVAL, label="author.open", skip=True) or {}
    if not res.get("ok"):
        log.info("详情页没有作者本人主页链接 uid=%s reason=%s", uid, res.get("reason"))
        return {}
    fresh = await _wait_user_page(page, before, tries=8)
    if fresh is None:
        fresh = await _open_profile_page(page, res.get("href") or "")
    if fresh is None:
        cur = _page_url_now(page)
        if "/user/profile/" in cur:
            # 极端情况：链接在当前页内跳转——就地取，取完退回详情页
            if uid and _uid_from_profile_url(cur) not in ("", uid):
                log.warning("作者页 uid 不匹配，丢弃 期望=%s 实际=%s", uid, _uid_from_profile_url(cur))
                return {}
            try:
                await asyncio.sleep(0.8)
                return await _extract_user(page)
            finally:
                await page.go_back()
        log.info("作者页未出现，跳过作者资料 uid=%s url=%s", uid, cur)
        return {}
    try:
        got = _uid_from_profile_url(fresh.url)
        if uid and got and got != uid:
            log.warning("作者页 uid 不匹配，丢弃 期望=%s 实际=%s", uid, got)
            return {}
        await asyncio.sleep(1)
        return await _extract_user(fresh)
    finally:
        await cdp_call(_close_page_safe(fresh, account_id),
                       timeout=CDP_QUICK, label="author.close", skip=True)


async def _read_author_fans(page, uid: str = "", account_id: str = "default") -> str:
    """只取粉丝数（作者资料取完页已关，这里不再需要回退）。取不到返回空串，不伪造 0。"""
    u = await _read_author_profile(page, uid, account_id)
    fans = (u or {}).get("fans", "") or ""
    if fans:
        log.info("作者页粉丝 uid=%s fans=%s", uid, fans)
    return fans


async def _assert_logged_in(page, account_id: str) -> None:
    """采集动作前的最小登录自检。

    详情提取原本没有任何登录闸：会话失效后点不开卡片，只返回空 dict，调用方按
    「缺字段降级」处理——整批空跑完还报「采集完成」，与「需要登录就结束本次采集」
    正好相反。这里补一道闸，命中即抛 BrowserLoginRequiredError，由上层收尾并通知左窗。
    """
    if await _page_needs_login(page):
        _check_login_wall(account_id, "小红书要求登录（页面已回落游客态）")
    if not await _session_cookie_alive(page):
        _check_login_wall(account_id, "小红书要求登录（会话 cookie 已失效）")


async def note(note_id: str, xsec_token: str = "", wait_ms: int = 5000,
               account_id: str = "default", with_author: bool = False) -> dict:
    """详情页数据：在采集搜索页里点开目标卡片提取（不用 URL、不刷新）。

    没有活动搜索页（采集已结束/被关）时返回 {}，由调用方按缺字段降级——
    绝不为了补字段另开 /explore/{id} 或 /search_result/{id}。
    `with_author=True` 时顺带进作者主页取粉丝数（唯一安全位置就是详情页内）。
    """
    page = _active_page(account_id)
    if page is None:
        cached = _ACTIVE_PAGES.get(account_id)
        log.info("无活动搜索页，跳过详情 note_id=%s（规则禁止 URL 进详情页）keys=%s closed=%s",
                 note_id, list(_ACTIVE_PAGES),
                 "none" if cached is None else cached.is_closed())
        return {}
    await _assert_logged_in(page, account_id)
    before = {id(p) for p in page.context.pages}
    if not await _open_note_card(page, note_id):
        log.info("详情卡片未命中 note_id=%s url=%s", note_id, _page_url_now(page))
        return {}
    await asyncio.sleep(wait_ms / 1000)
    try:
        d = await _read_note_detail(page)
        log.info("详情提取 note_id=%s url=%s fields=%s", note_id,
                 _page_url_now(page),
                 sorted(d) if isinstance(d, dict) else type(d).__name__)
        if d and with_author:
            d["author_fans"] = await _read_author_fans(page, d.get("user_id", ""), account_id)
        return d
    finally:
        await _back_to_list(page)
        n = await _close_stray_pages(page, before, account_id)
        if n:
            log.info("关闭采集过程新开页 %s 个", n)


async def _xhs_page(account_id: str = "default"):
    """取引擎里任一未关闭的小红书页（发帖/看自己主页这类非采集流程用）。"""
    try:
        ctx = await _ctx(account_id)
    except Exception:  # noqa: BLE001
        return None
    for p in ctx.pages:
        try:
            if p.is_closed():
                continue
            if "xiaohongshu.com" in (p.url or ""):
                return p
        except Exception:  # noqa: BLE001
            continue
    return None


async def _back_home_quiet(page) -> None:
    """抓完退回主页；失败不抛，免得把已经抓到的结果盖掉。"""
    try:
        if "/explore/" in (page.url or ""):
            await page.go_back()
            await asyncio.sleep(1)
    except Exception:  # noqa: BLE001
        pass


async def note_url(url: str, wait_ms: int = 5000, account_id: str = "default") -> dict:
    """按完整链接打开笔记页并提取详情（**唯一例外**：链接由用户本人提供，无页内入口可达）。"""
    page = await _load(url, wait_ms, account_id)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _close_page_safe(page, account_id)


async def user(uid: str, account_id: str = "default") -> dict:
    """作者资料：只在详情页内点作者（平台会新开作者页，取完即关闭，无历史回退）。

    搜索页上点作者会污染历史栈（后续 `go_back()` 会把整页退回 /explore 首页，
    后续详情全部点不到卡片），所以非详情页一律跳过，调用方按 fans=0 降级。
    """
    page = _active_page(account_id)
    if page is None:
        log.info("无活动搜索页，跳过作者页 uid=%s", uid)
        return {}
    if _page_position(page) != "detail":
        log.info("作者页需在详情页内打开，当前页=%s 跳过 uid=%s", _page_url_now(page), uid)
        return {}
    u = await _read_author_profile(page, uid, account_id)
    log.info("作者页提取 uid=%s 命中=%s", uid, bool(u))
    return u


class ClientCollectError(RuntimeError):
    """评论采集前置条件不满足（无搜索页 / 卡片不在结果里），带用户可读原因。

    与「采集成功但 0 条评论」区分开：后者返回空列表，前者必须让界面说清为什么。
    """


class _RiskLimited(RuntimeError):
    """当前标签被平台判为异常访问（落到 website-login/error）。

    2026-11-13 实测：被拦的参数里 `rejectUrl` 指向 `v2/comment/page`，
    `error_code=300013`、提示「检测到异常访问…本次访问已被限制」。
    这种拦截绑在当前标签上——换一个新标签重新进详情页即可恢复（实测有效），
    所以上层捕获它之后要「关掉这个页、新开一页重来」，不要原地重试。
    """


async def clients(note_id: str, xsec_token: str = "", account_id: str = "default",
                  keyword: str = "", filters: dict | None = None,
                  limit: int = 0, budget: float = 0.0, resume: bool = False,
                  known: list | None = None) -> dict:
    """评论区互动用户：在采集页点开目标卡片 → 展开/滚动评论区 → 提取 → 返回。

    点卡片与详情补全走同一条路径（真鼠标点可见卡片，平台才会补 xsec_token）。

    搜索页是"点卡片采评论"的唯一入口。采集完/被关掉/重启后没有活动搜索页时，
    过去是静默返回空，用户会误以为"这条笔记没人评论"。现在按库里关键词重开一次搜索页
    （仅搜索、不采集，keep_page 留页），仍失败则抛 ClientCollectError 交界面明说。

    filters：重开搜索页时必须带上原采集用的筛选条件（排序/类型/时间/范围/距离）。
    不带条件重开得到的是综合排序的混合结果，原卡片多半不在里面，于是误报"笔记已不在
    搜索结果里"（2026-11-08 真机踩到：采集用「图文+最新」，重开无筛选 → 卡片未命中）。

    主路径：**在活动搜索页上用真鼠标点开那张卡片**（用户拍板：能点的就别拼 URL 直开，
    URL 只允许开 /login）。点开失败、且手里有 xsec_token 时才兜底直开一次。
    """
    limit = int(limit or _COMMENT_LIMIT)
    budget = float(budget or _COMMENT_BUDGET)
    seed: dict = {}
    skip = 0
    if resume:
        sess = _COMMENT_SESSIONS.get(note_id)
        if sess and not sess["page"].is_closed() and _page_position(sess["page"]) == "detail":
            return await _drain_with_session(sess, limit, budget)
        # 会话页已经不在原笔记（被关掉/被平台跳走/这一页翻到底了）：不甩报错给用户，
        # 改为重新打开本篇，把库里已采过的用户当"已知集合"，开头快进滚过已采深度，
        # 再从新评论接着采（2026-11-23 用户实测：点继续直接报"无法接着采"+按钮消失）。
        _COMMENT_SESSIONS.pop(note_id, None)
        seed = _acc_map(known or [])
        skip = len(seed)
        log.info("续采会话失效，改为重开本篇 note_id=%s known=%d", note_id, skip)

    # 同一篇重新采集（非续采）：上一次到上限留下的详情页要先退回列表，否则点卡片落空
    stale = _COMMENT_SESSIONS.pop(note_id, None)
    if stale and not stale["page"].is_closed() and _page_position(stale["page"]) == "detail":
        await _back_to_list(stale["page"])

    page = _active_page(account_id)
    if page is None and keyword:
        log.info("无活动搜索页，按关键词重开 note_id=%s kw=%s filters=%s", note_id, keyword, filters)
        await search(keyword, limit=1, account_id=account_id, keep_page=True,
                     filters=filters or {})
        page = _active_page(account_id)
    if page is None:
        raise ClientCollectError("当前没有打开的搜索结果页，无法进入评论区。请先在左侧点「开始采集」打开搜索结果，再点「采集客户」。")
    if not await _open_note_card(page, note_id):
        # 搜索结果可能还没渲染全、或目标卡在后面几屏：再滚屏找几次，仍找不到才判定真的不在。
        # （平台每次搜索结果是不同批次，笔记可能已漂走——那种情况只能让用户重新采集。）
        for _ in range(3):
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2.5)
            if await _open_note_card(page, note_id):
                break
        else:
            # 兜底：卡片点不开（搜索批次已漂走）但手里有 token 时才直开一次。
            if xsec_token:
                direct = await _open_detail_page(note_id, xsec_token, account_id)
                if direct is not None:
                    try:
                        users, capped = await _drain_comments(direct, limit, budget)
                        return _finish_session(note_id, direct, {}, users, capped)
                    except _RiskLimited:
                        await _risk_relogin(direct, account_id, note_id)
            log.info("评论卡片未命中 note_id=%s url=%s", note_id, _page_url_now(page))
            raise ClientCollectError(
                "这条笔记已不在当前搜索结果里（平台每次搜索结果会变化，尤其按「最新」排序）。"
                "请重新采集一批笔记后再点「采集客户」。")
    await asyncio.sleep(4)
    _ensure_detail_page(page)
    try:
        users, capped = await _drain_comments(page, limit, budget, acc=seed or None, skip_known=skip)
    except _RiskLimited:
        await _risk_relogin(page, account_id, note_id)
    if capped:
        # 到上限不返回列表：留着这一页停在当前位置，用户点「继续采集」时接着往下滚
        _COMMENT_SESSIONS[note_id] = {"page": page, "acc": _acc_map(users)}
        return {"users": users, "capped": True, "added": max(0, len(users) - skip)}
    await _back_to_list(page)
    return {"users": users, "capped": False, "added": max(0, len(users) - skip)}


def _ensure_detail_page(page) -> None:
    """点开卡片后必须真的进了详情页，否则前置失败要摆在明面上。

    2026-11-09 真机复现：在重开的搜索列表上点了卡片，详情层没出现，旧实现继续在
    列表页上跑 _drain_comments，返回 {"total":0,"users":[]} —— 界面显示「这条没人
    评论」，把前置条件失败伪装成业务结果。用户看到的就是「采集客户没反应」。
    """
    if _page_position(page) != "detail":
        raise ClientCollectError(
            "没能打开这条笔记的详情页（卡片点击落空，页面仍停在列表）。"
            "本次未采到评论用户——请重试，或重新采集一批笔记后再点「采集客户」。")


_COMMENT_MAX_ROUNDS = 70      # 滚动保险上限（防死循环），不是采集配额
_COMMENT_LIMIT = 150          # 单次采集条数上限（评论多的笔记分段采，防超时/踩风控）
_COMMENT_BUDGET = 90.0        # 单次采集时间预算（秒）：到点先停，界面提示还能继续采
_COMMENT_SESSIONS: dict[str, dict] = {}   # note_id → {page, acc}：到上限后留着页继续采
_COMMENT_IDLE_STOP = 4        # 连续 4 轮没有新批就判定到底
_COMMENT_WHEEL_STEP = 850     # 每次滚轮位移≈一屏（真人一屏的量，不是一次跳到底）
_COMMENT_WHEEL_GAP = (1.2, 1.8)   # 两次滚轮之间停顿（秒），带抖动
_COMMENT_ROUND_GAP = (1.6, 2.4)   # 每轮之间停顿（秒），看内容的时间


def _acc_key(u: dict) -> str:
    return str(u.get("uid", "")) + "|" + (u.get("comment") or "")[:24]


def _acc_map(users: list) -> dict:
    return {_acc_key(u): u for u in users or []}


def _finish_session(note_id: str, page, acc: dict, users: list, capped: bool,
                    start_len: int = 0) -> dict:
    """统一收尾：到上限就存会话留页，否则清会话。added=本批新增（续采只算新面孔）。"""
    if capped:
        _COMMENT_SESSIONS[note_id] = {"page": page, "acc": acc or _acc_map(users)}
    else:
        _COMMENT_SESSIONS.pop(note_id, None)
    return {"users": users, "capped": bool(capped), "added": max(0, len(users) - start_len)}


async def _drain_with_session(sess: dict, limit: int, budget: float) -> dict:
    """续采：沿用上次留在详情页的那一页，从当前位置接着滚，累积去重。"""
    page = sess["page"]
    acc = sess["acc"]
    start_len = len(acc)
    users, capped = await _drain_comments(page, limit, budget, acc=acc)
    added = max(0, len(users) - start_len)
    # 一个新面孔都没拿到（平台把这篇限频了）：页面留着，用户过几分钟点「继续」还能接上
    keep = capped or added == 0
    if not keep:
        await _back_to_list(page)
    return _finish_session_no_key(sess, page, users, capped, start_len, keep)


def _finish_session_no_key(sess: dict, page, users: list, capped: bool,
                           start_len: int = 0, keep: bool | None = None) -> dict:
    keep = bool(capped) if keep is None else keep
    if keep:
        sess["page"] = page
    else:
        for k, v in list(_COMMENT_SESSIONS.items()):
            if v is sess:
                _COMMENT_SESSIONS.pop(k, None)
    return {"users": users, "capped": bool(capped), "added": max(0, len(users) - start_len)}


async def _drain_comments(page, limit: int = _COMMENT_LIMIT, budget: float = _COMMENT_BUDGET,
                          acc: dict | None = None, skip_known: int = 0) -> tuple[list, bool]:
    """评论区互动用户：真实滚轮滚到底 + 展开折叠回复 + 边滚边抓。

    （2026-11-13 真机取证，替换旧实现）两条硬事实：
    1. 评论容器 `.note-scroller` 自身 scrollHeight == clientHeight（4581/4581），
       JS 写 scrollTop 完全无效——scrollTop 恒 0、评论恒 19 条，575 条评论的
       笔记因此只采到 19 位用户。必须用 page.mouse.wheel 派发真实滚轮事件，
       实测 25 秒内主评论 10 → 457 条（每批 10 条）。
    2. 滚到后面评论区会整块从 DOM 卸载（457 条后 .comment-item 归零），
       只在最后提取一次必然拿到空列表（实测 total=0）。所以每加载一批就
       抓一次、按 uid+评论去重累积，滚过头也不丢已抓到的用户。

    单次上限：条数（limit）与时间（budget）任一先到就停，返回 capped=True。
    评论几百条的笔记一次滚到底要好几分钟、也更容易踩风控，所以分段采：
    到上限时调用方保留这一页，用户点「继续采集」再从当前位置接着滚。
    判定"真的到底"仍靠"连续 4 轮没有新批"。
    """
    if "website-login/error" in (_page_url_now(page) or ""):
        raise _RiskLimited("详情页被平台判为异常访问")
    await _expand_comment_folds(page)          # 先展开「查看更多评论」类入口

    # 鼠标必须落在「当前可见」的评论上，滚轮才会滚评论列表而不是笔记正文。
    # 不能 hover 第一条：续采时那条在视口外，Playwright 会自动 scrollIntoView
    # 把列表拉回顶部（2026-11-23 实测：续采每轮 n 恒等于 336、再也采不到新评论）。
    await _aim_comment_area(page)

    acc = acc if acc is not None else {}
    if skip_known:
        # 重开本篇续采：先用快进滚轮跳过已经采过的深度（不做提取，省时间），
        # 速度≈10 条/轮，已知 N 条就滚 N/8 轮（留余量），上限 60 轮防跑飞。
        wheels = min(60, max(6, int(skip_known / 8)))
        log.info("[comment.drain] 快进跳过已采区 known=%d wheels=%d", skip_known, wheels)
        for _ in range(wheels):
            await page.mouse.wheel(0, _COMMENT_WHEEL_STEP)
            await asyncio.sleep(0.45)
        await asyncio.sleep(1.2)
    prev = -1
    idle = 0
    capped = False
    start_len = len(acc)          # 续采时 acc 里已有上一批，上限只算"本批新增"
    t0 = time.monotonic()
    for _ in range(_COMMENT_MAX_ROUNDS):
        if (len(acc) - start_len) >= limit or (time.monotonic() - t0) >= budget:
            capped = True
            log.info("[comment.drain] 到上限 limit=%s budget=%s 本批新增=%d 累计=%d",
                     limit, budget, len(acc) - start_len, len(acc))
            break
        # 真人节奏：一次滚一屏、停一下再看，别 4 连跳、也别 0.2s 一拍
        for k in range(2):
            await page.mouse.wheel(0, _COMMENT_WHEEL_STEP)
            if k == 0:
                await asyncio.sleep(random.uniform(*_COMMENT_WHEEL_GAP))
        await asyncio.sleep(random.uniform(*_COMMENT_ROUND_GAP))

        cur = await cdp_call(page.evaluate(_COMMENT_COUNT_JS), timeout=CDP_EVAL,
                             label="comment.count", skip=True)
        n = (cur or {}).get("items", 0)
        # 诊断：确认滚动到底有没有真的触发加载（UI 触发时右窗可能停在搜索页浮层，
        # 评论只在侧栏、滚不出全量——2026-11-13 实测 575 条只拿到 85 条）
        url_now = _page_url_now(page) or ""
        log.info("[comment.drain] r=%d n=%d url=%s", _, n, url_now[:70])
        # 滚到一半被平台跳「异常访问」页：评论永不再加载，绝不能当"这篇没人评论"返回空
        if "website-login/error" in url_now:
            raise _RiskLimited("详情页被平台判为异常访问")

        if n == 0 and prev > 0:                # 滚过头，评论区被卸载 → 回滚找回
            for _ in range(6):                 # 回滚也按真人节奏，别瞬间弹回
                await page.mouse.wheel(0, -_COMMENT_WHEEL_STEP)
                await asyncio.sleep(random.uniform(0.9, 1.4))
            await asyncio.sleep(random.uniform(1.2, 1.8))
            continue

        if n != prev and n > 0:                # 来了新一批，立刻抓一次并累积
            await injections.inject_once(page, "extract")
            batch = await page.evaluate("() => xhsExtractComments()") or []
            for u in batch:
                acc.setdefault(_acc_key(u), u)
            prev = n
            idle = 0
            continue

        idle += 1
        if idle >= _COMMENT_IDLE_STOP:
            break

    if not capped:                              # 到上限时不再展开折叠，留给下一批
        clicked = await _expand_comment_folds(page)  # 到底后再展开折叠的二级回复
        if clicked:                             # 真展开出新回复 → 补抓一次
            await injections.inject_once(page, "extract")
            batch = await page.evaluate("() => xhsExtractComments()") or []
            for u in batch:
                acc.setdefault(_acc_key(u), u)
    return list(acc.values()), capped


async def _expand_comment_folds(page, batch: int = 15, max_batches: int = 8) -> int:
    """反复点开折叠入口（「展开 N 条回复」「查看更多评论」）。

    每次最多点 batch 个、最多 max_batches 批：一次全点几百个折叠会瞬间拉起
    大量渲染和请求，既卡页面也踩风控。返回累计点击数。
    """
    total = 0
    for _ in range(max_batches):
        n = await cdp_call(page.evaluate(_COMMENT_EXPAND_JS, {"batch": batch}),
                           timeout=CDP_EVAL, label="comment.expand", skip=True)
        n = int(n or 0)
        await asyncio.sleep(1.2)
        total += n
        if n < batch:
            break
    return total


# 评论区中心坐标（hover 失败时的兜底）：钳制在可视区内
_COMMENT_AIM_JS = """() => {
  // 取"当前视口内面积最大"的评论块中心，绝不 scrollIntoView（那会把列表拉回顶部）
  const items = [...document.querySelectorAll('.comment-item')];
  let best = null, area = 0;
  for (const el of items) {
    const r = el.getBoundingClientRect();
    const h = innerHeight, w = innerWidth;
    const visH = Math.min(r.bottom, h) - Math.max(r.top, 0);
    const visW = Math.min(r.right, w) - Math.max(r.left, 0);
    if (visH > 24 && visW > 60) {
      const a = visH * visW;
      if (a > area) { area = a; best = r; }
    }
  }
  if (best) {
    return { x: Math.round(best.left + best.width / 2), y: Math.round(Math.max(best.top, 30) + 12) };
  }
  const c = document.querySelector('.interaction-container');
  if (!c) return null;
  const r = c.getBoundingClientRect();
  if (r.width <= 0 || r.bottom < 0 || r.top > innerHeight) return null;
  return { x: Math.round(Math.min(Math.max(r.left + r.width / 2, 20), innerWidth - 20)),
           y: Math.round(Math.min(Math.max(r.top + Math.min(r.height / 2, 120), 60), innerHeight - 60)) };
}"""


async def _aim_comment_area(page) -> bool:
    """把鼠标移到视口内可见的评论区（不触发滚动），滚轮才只滚评论列表。"""
    box = await cdp_call(page.evaluate(_COMMENT_AIM_JS), timeout=CDP_EVAL,
                         label="comment.aim", skip=True)
    if not box:
        return False
    await page.mouse.move(box["x"], box["y"])
    return True


_COMMENT_COUNT_JS = """() => ({
  items: document.querySelectorAll('.comment-item').length,
  parents: document.querySelectorAll('.parent-comment').length,
})"""

_COMMENT_EXPAND_JS = """(args) => {
  const batch = (args && args.batch) || 15;
  const cands = [];
  document.querySelectorAll('.show-more').forEach(e => { if (!e.dataset.xhsHopped) cands.push(e); });
  document.querySelectorAll('button, [role="button"]').forEach(e => {
    const t = (e.innerText || '').trim();
    if (t.length < 20 && /(展开|查看)?全部评论|查看更多评论/.test(t) && !e.dataset.xhsHopped) cands.push(e);
  });
  let clicked = 0;
  for (const e of cands) {
    if (clicked >= batch) break;
    try { e.dataset.xhsHopped = '1'; e.click(); clicked++; } catch (_) {}
  }
  return clicked;
}"""


# ===========================================================================
# O1-5 去联系：打开用户主页 → 点聊天按钮 → 落到私信会话页（不代发消息）
# ===========================================================================

_CONTACT_IM_BTN = "button.xhs-user-im-btn"
_CHAT_URL_PREFIX = "https://www.xiaohongshu.com/chat?openUid="


class ContactOpenError(RuntimeError):
    """去联系前置失败（主页链接无效 / 聊天按钮没出现 / 私信页没落地），带用户可读原因。"""


async def _wait_chat_page(ctx, want_url: str, timeout: float):
    """点击后等出现 /chat?openUid=<uid> 会话页；超时返回 None。

    平台会复用已存在的同 openUid target（不新开），所以按 URL 匹配、不限新旧。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for pg in ctx.pages:
            try:
                if (pg.url or "").startswith(want_url):
                    return pg
            except Exception:  # noqa: BLE001（页面还在导航：下轮再看）
                continue
        await asyncio.sleep(0.4)
    return None


async def open_contact(profile_url: str, account_id: str = "default") -> str:
    """在采集窗打开用户主页并点聊天按钮，落到私信会话页，返回会话页 URL。

    工具只把人带到可输入的会话页：不代写话术、不自动发送（用户口径）。
    任一步没落地就抛 ContactOpenError，由接口转 409 明说，不静默假装成功。

    真机验证（2026-11-09）：主页头部「关注」旁的纯图标按钮 `button.xhs-user-im-btn`
    点击后会新开 target `/chat?openUid=<uid>`，页内有输入框 `.xhs-im-input-bar-editor`。
    """
    if not profile_url or "/user/profile/" not in profile_url:
        raise ContactOpenError("缺少有效的用户主页链接，无法打开私信")
    # profile 末段即用户 uid，聊天页用它作 openUid（平台可能复用已存在的同 uid 会话页）
    uid = profile_url.split("/user/profile/", 1)[1].split("?", 1)[0].strip("/")
    want_chat = _CHAT_URL_PREFIX + uid
    ctx = await _ctx(account_id)
    page = await ctx.new_page()
    try:
        await cdp_call(page.goto(profile_url, wait_until="domcontentloaded"),
                       timeout=CDP_NAV, label="contact.profile", skip=True)
        try:
            # 外层 15s > 内部 12s：wait_for_selector 能完整跑完；超时/冻结才真正报错（不 skip）
            await cdp_call(page.wait_for_selector(_CONTACT_IM_BTN, timeout=12000),
                           timeout=CDP_NAV, label="contact.im_btn")
        except Exception as exc:  # noqa: BLE001（playwright 内部超时 / 外层 CdpTimeoutError 同口径）
            raise ContactOpenError(
                "用户主页未加载出聊天按钮（可能未登录或被风控拦截）"
            ) from exc
        await cdp_call(page.click(_CONTACT_IM_BTN), timeout=CDP_EVAL,
                       label="contact.im_click")
        chat_page = await _wait_chat_page(ctx, want_chat, timeout=10)
        if chat_page is None:
            raise ContactOpenError("点击聊天按钮后未打开私信会话页")
        await cdp_call(chat_page.bring_to_front(), timeout=CDP_QUICK,
                       label="contact.chat_front", skip=True)
        return chat_page.url
    except ContactOpenError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ContactOpenError(f"打开私信会话失败：{str(exc)[:80]}") from exc


# ===========================================================================
# 引擎生命周期（server shutdown / 一次性动作）
# ===========================================================================

async def stop():
    """关闭全部引擎 + Playwright 实例（后端退出钩子）。"""
    await pool.close_all()


# ===========================================================================
# 填入小红书发布页（创作者中心「上传图文」）：只填，不点发布
# ===========================================================================
class FillXhsError(RuntimeError):
    """填入发布页失败（没进到编辑页 / 图片传不上去 / 字段写不进去）。"""


_CREATOR_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish"
_FILL_TITLE_SEL = 'input.d-text[placeholder*="填写标题"]'
_FILL_BODY_SEL = "div.tiptap.ProseMirror"
_FILL_IMAGE_INPUT = 'input.upload-input[type="file"]'

# 发布页默认停在「上传视频」，点 tab 切到「上传图文」；已在图文时点不到、无副作用
_SWITCH_IMAGE_TAB_JS = """() => {
  for (const e of [...document.querySelectorAll('.creator-tab')]) {
    if ((e.innerText || '').trim() === '上传图文') { e.click(); return true; }
  }
  return false;
}"""


async def _creator_page(ctx):
    """取当前打开着的创作者中心页（没有就 None）。"""
    for p in ctx.pages:
        if not p.is_closed() and "creator.xiaohongshu.com" in (p.url or ""):
            return p
    return None


async def _ensure_publish_page(account_id: str = "default"):
    """取创作者中心发布页：已开着就复用，否则新开一个到发布页。"""
    ctx = await _ctx(account_id)
    creator = await _creator_page(ctx)
    if creator is not None:
        return creator
    creator = await ctx.new_page()
    await cdp_call(creator.goto(_CREATOR_PUBLISH_URL, wait_until="domcontentloaded"),
                   timeout=CDP_NAV, label="fill.creator")
    return creator


async def _fill_images(creator, files: list[str]) -> None:
    if not files:
        return
    await cdp_call(creator.locator(_FILL_IMAGE_INPUT).set_input_files(files),
                   timeout=CDP_NAV, label="fill.images")
    await asyncio.sleep(3)


async def _fill_text(creator, title: str, body: str) -> None:
    await creator.fill(_FILL_TITLE_SEL, (title or "").strip()[:20])
    if body:
        await cdp_call(creator.click(_FILL_BODY_SEL), timeout=CDP_EVAL, label="fill.body_click")
        await creator.keyboard.insert_text(body[:1000])


async def fill_write_page(title: str, body: str, images: list[str],
                          account_id: str = "default") -> str:
    """把标题/正文/标签/图片填进创作者中心发布页，**不点「发布」**。

    真机验证（2026-11-13）：发布页 = creator.xiaohongshu.com/publish/publish，
    标题 `input.d-text[placeholder*="填写标题"]`、正文 tiptap `div.tiptap.ProseMirror`、
    图片 `input.upload-input[type=file]`（可多选）。可见范围/发布账号由平台自己管，不在此设置。
    """
    creator = await _ensure_publish_page(account_id)
    await asyncio.sleep(2)
    # 等页面侧栏 tab 渲染出来再切「上传图文」（刚打开时 tab 还没出来）
    try:
        await creator.wait_for_selector(".creator-tab", timeout=12000)
    except Exception as exc:  # noqa: BLE001
        raise FillXhsError("发布页没加载出来（可能未登录或被风控拦截）") from exc
    try:
        await creator.evaluate(_SWITCH_IMAGE_TAB_JS)
    except Exception:  # noqa: BLE001  # 已在图文页，tab 点不到
        pass
    await asyncio.sleep(1.5)
    if not images:
        raise FillXhsError("图文笔记至少要有一张图片：请先补图再填入")
    await _fill_images(creator, images)
    # 标题/正文编辑区在上传图片之后才出现，这里等它渲染
    try:
        await creator.wait_for_selector(_FILL_TITLE_SEL, timeout=15000)
    except Exception as exc:  # noqa: BLE001
        raise FillXhsError("图片上传后没出现标题编辑区（可能上传失败或被风控拦截）") from exc
    await _fill_text(creator, title, body)
    return "已把标题/正文/标签/图片填进小红书发布页，请确认后手动点「发布」"
