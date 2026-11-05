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
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from playwright.async_api import async_playwright

from src import config, injections, logger as xlog, risk
from src.engine_types import builtin_cft_candidates

# 主屏分辨率缓存（R-6.22：双窗口左右分屏布局用）。probe_screen_size() 首次探测后缓存。
_SCREEN: tuple[int, int] | None = None

def probe_screen_size() -> tuple[int, int]:
    """探测主屏分辨率 (宽, 高)。macOS：system_profiler 第一行 Resolution（主屏优先）；
    失败回退 Finder 合成桌面 bounds；再失败回退 1920x1200。结果模块级缓存。

    用途：启动时壳窗口（主 APP）放主屏左半、采集调试窗口放右半，两个窗口
    等宽并排、互不重叠（窗口宽 = min(1280, 主屏宽 // 2)）。
    """
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    w, h = 1920, 1200
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        for line in out.splitlines():
            if "Resolution:" in line and " x " in line:
                nums = re.findall(r"(\d+)\s*[xX]\s*(\d+)", line)
                if nums:
                    w, h = int(nums[0][0]), int(nums[0][1])
                    break
    except Exception:  # noqa: BLE001（探测失败走回退）
        pass
    _SCREEN = (w, h)
    return _SCREEN

def shell_window_geometry() -> tuple[int, int, int, int]:
    """双窗口并排布局（R-6.22）：返回 (壳窗口宽, 壳窗口高, 壳窗口 left, 采集窗口 left)。

    主屏宽 SW：壳窗口宽 = min(1280, SW // 2)，位置 (0, 0)（主屏左半）；
    采集调试窗口同宽，位置 (win_w, 0)（主屏右半）。两窗等宽并排、互不重叠。
    """
    sw, _ = probe_screen_size()
    win_w = min(1280, sw // 2)
    return win_w, 800, 0, win_w

# R-6.19：Tab2（采集骨架）创建临界区锁——keeper 兜底轮询与 server 开关联动
# 并发创建会双开 explore 骨架；共享同一锁串行化「检查+创建」。
TAB2_GUARD_LOCK = asyncio.Lock()

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

# 防自动化检测 stealth 注入脚本已收敛到 injections 注册表（R-6.30a）：
# injections.get("stealth")——引擎 context add_init_script 启动时注入一次。



async def _get_pw():
    """全局共享一个 Playwright 实例（多个 persistent context = 多个独立浏览器进程）。"""
    global _pw, _pw_lock
    if _pw_lock is None:
        _pw_lock = asyncio.Lock()
    async with _pw_lock:
        if _pw is None:
            _pw = await async_playwright().start()
        return _pw




def _is_blocked_risk_text(txt: str) -> bool:
    """R-6.37i 拆自 _debug_window_blocked：正文是否命中风控/需验证关键词。"""
    return any(k in txt for k in ("登录后查看", "风控拦截", "需人工扫码",
                                  "人工验证", "验证后重试", "登录超限",
                                  "请重新登录", "登录已过期"))




async def _any_blocked_page(ctx) -> bool:
    """R-6.37i：任一小红书页正文命中风控关键词则 True。"""
    for pg in ctx.pages:
        if "xiaohongshu" not in (pg.url or ""):
            continue
        try:
            txt = (await pg.inner_text("body")) or ""
        except Exception:  # noqa: BLE001
            continue
        if _is_blocked_risk_text(txt):
            return True
    return False


_SESSION_RESTORE_FILES = ("Current Session", "Current Tabs")


def _clear_session_restore(user_data_dir: str) -> None:
    """R-6.48s：启动前清理 Chrome 会话恢复——上次异常退出（exit_type=Crashed，
    如 pkill 停实例）时 Chrome 默认恢复上次全部 tab（可能停在 xiaohongshu.com
    登录页/首页）并弹「要恢复页面吗？Chromium未正确关闭」横幅，导致启动先闪现
    小红书页再被导航到 APP（用户反馈「启动看到小红书一闪而过」）。
    删会话恢复文件（Default/Sessions/*、Current Session、Current Tabs），并把
    Preferences.exit_type 修正为 Normal（R-6.49b：根除恢复横幅，防止用户点
    「恢复」把上次会话页面（含 explore）拉回）。
    不动 Cookies/DB——登录态由 _replay_saved_cookies 以 cookies.json 为权威源
    回注，跨重启保留不受影响（R-6.6 机制）。"""
    default_dir = os.path.join(user_data_dir, "Default")
    sessions_dir = os.path.join(default_dir, "Sessions")
    try:
        if os.path.isdir(sessions_dir):
            for name in os.listdir(sessions_dir):
                try:
                    os.remove(os.path.join(sessions_dir, name))
                except OSError:
                    pass
    except OSError:
        pass
    for name in _SESSION_RESTORE_FILES:
        try:
            p = os.path.join(default_dir, name)
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    # R-6.49b：exit_type=Crashed → Normal（防「要恢复页面吗？Chromium未正确关闭」横幅）
    try:
        prefs_path = os.path.join(default_dir, "Preferences")
        if os.path.isfile(prefs_path):
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            changed = False
            if prefs.get("exit_type") not in (None, "Normal"):
                prefs["exit_type"] = "Normal"
                changed = True
            prof = prefs.get("profile")
            if isinstance(prof, dict) and prof.get("exit_type") not in (None, "Normal"):
                prof["exit_type"] = "Normal"
                changed = True
            if changed:
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False)
    except (OSError, ValueError):
        pass


class AccountEngine:
    """单账号引擎：持久化上下文 + 生命周期 + 页面管理。"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        acc = config.get_account(account_id) or config.add_account(account_id)
        self.user_data_dir = config.expand(acc.get("user_data_dir", ""))
        self.role = acc.get("role", "mixed")
        self._context = None
        self._lock = asyncio.Lock()
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
            # R-6.22j（用户拍板）：移除「引擎启动失败 20s 冷却」——该防抖本意是
            # 避免启动失败后反复重试；但用户实测（重登录场景多、封号从未出现）
            # 认为无必要，且调试/重启时该冷却会造成「启动冷却中」误拦截与空白窗口
            # 观感。启动失败直接抛错，由上层（任务 error / 用户重试）处置。

            pw = await _get_pw()
            try:
                self._context = await self._launch_context(pw)
                xlog.audit(self.account_id, action="engine_start", ok=True,
                           detail=f"dir={config.home_relative(self.user_data_dir)}")
                return self._context
            except Exception as e:  # noqa: BLE001
                # R-6.22j：移除启动失败 20s 冷却（用户拍板，见 context() 入口注释）
                xlog.audit(self.account_id, action="engine_start", ok=False,
                           detail=f"启动失败: {str(e)[:200]}")
                raise RuntimeError(f"账号 {self.account_id} 引擎启动失败: {e}") from e

    async def _launch_context(self, pw):
        """R-6.36e 拆自 context()：构建并 launch_persistent_context（含 Cookie 回注/stealth）。"""
        os.makedirs(self.user_data_dir, exist_ok=True)
        # R-6.48s：启动前清会话恢复文件——上次异常退出（exit_type=Crashed）Chrome
        # 恢复旧 tab（小红书页）→ 启动闪现小红书再被导航 APP；清后启动仅默认空白 tab。
        _clear_session_restore(self.user_data_dir)
        # 关键：launch_persistent_context（内部 stdio 管道，无 TCP 调试端口，
        # 从机制上不触发「允许远程调试」授权流程；解密在浏览器进程内部，免钥匙串）
        # R 批 v1.4：headless 无头化（外部零 Chrome 窗口）+ stealth 强化
        #  - viewport 1280x800：桌面宽屏布局（小红书桌面版），登录小窗/调试镜像同尺寸
        #  - user_agent：无头时覆盖 HeadlessChrome 特征（风控指纹）
        #  - AutomationControlled：关掉 CDP 来源的 webdriver 标记（与 init script 双保险）
        #  - headless=False 兜底档：风控敏感环节可临时切后台可见窗口（config.headless）
        cfg = config.get_config()
        headless = bool(cfg.get("headless", True))
        # R 批 v1.5：引擎带固定 CDP 调试端口（config.engine_port()，默认 9223）供
        # e1/t 系列 UI 测试 connect_over_cdp 连接（同一实例=同一登录态 profile）；
        # 旧「9223 外部可见 Chrome」测试路径退役。Playwright 经 stdio 管道控制不受影响。
        eng_port = config.engine_port()
        # R-6.27：viewport 与窗口几何对齐——非无头（壳窗口）时页面布局宽度
        # 必须等于窗口宽度，否则窗口（主屏半宽，如 960）显示 1280 宽的布局，
        # 右侧内容被裁（用户反馈「尺寸没对齐」）。无头引擎保持 1280 采集布局。
        _geo_w, _geo_h = shell_window_geometry()[:2] if not headless else (1280, 800)
        launch_kwargs = dict(
            channel="chrome",
            headless=headless,
            viewport={"width": _geo_w, "height": _geo_h},
            args=[
                "--no-first-run", "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                f"--remote-debugging-port={eng_port}",
            ],
        )
        # 调试实时窗口（R-6.15 用户需求）：headless=False 时引擎出窗口，
        # 实时可见采集/登录操作过程（替代「截图映射」）。
        # R-6.17 曾用 --app 应用模式（无菜单栏/标签栏/地址栏）；R-6.18 用户拍板
        # 「壳窗口内两个固定 Tab」故移除；R-6.21 用户拍板还原「双窗口」并恢复
        # 壳窗口应用模式：窗口一=APP/登录（--app 壳窗口：无地址栏/无标签条，
        # 标签条上的 +/×/pinned 全部不再出现；keep_shell_tab 监护固定不可关），
        # 窗口二=采集（独立普通窗口，设置开关 collect_tab_visible 控制开/关，
        # shell_window.open_tab2_window 以 window.open 从壳窗口弹出——app 模式
        # 下 window.open 新 target 落到普通独立窗口，与双窗口语义一致）。
        # 注：XHS_SHELL_URL 由 launcher 注入（供 ensure_shell/监护/--app 使用）；
        # 未注入时（直接跑 server 调试）按 front_port 兜底拼。
        if not headless:
            shell_url = os.environ.get("XHS_SHELL_URL") or (
                f"http://127.0.0.1:{config.front_port()}/"
            )
            # R-6.22c：launch_persistent_context 的 --app 实测不生效（窗口仍为
            # 普通 Chrome 形态，带菜单栏/标签栏）。壳窗口应用模式改由
            # shell_window.ensure_shell 走 --app 命令行路由（_launch_app_window）
            # 弹出；此处保留 --window-size/--window-position 让启动初始普通窗口
            # R-6.49d：初始普通窗口移出屏幕（负坐标）——消除「普通→应用提升」时
            # 初始窗口在左半屏闪现两次的观感（CDP 取证：启动期两个 8791/ target，
            # 先普通后 --app，<1s 切换）。应用窗仍由 ensure_shell --app 路由弹主屏
            # 左半（--window-position=0,0 语义不变），初始窗屏幕外自生自灭。
            win_w, win_h, _, _ = shell_window_geometry()
            launch_kwargs["args"].append(f"--window-size={win_w},{win_h}")
            launch_kwargs["args"].append("--window-position=-4000,0")
        # R-6.11：内置 CFT（Chrome for Testing）优先——独立应用身份
        # （com.google.chrome.for.testing），引擎运行不再占用用户日常 Chrome 的
        # LaunchServices 单实例 → 根除「APP 开着时日常 Chrome 无法新建窗口」；
        # CFT 缺失时回退 channel="chrome"（系统 Google Chrome 兜底）。
        cft = builtin_cft_candidates()
        if cft:
            launch_kwargs["executable_path"] = cft
            launch_kwargs.pop("channel", None)
        log.info("引擎二进制: %s", cft or "channel=chrome（系统 Chrome）")
        if headless:
            launch_kwargs["user_agent"] = _HEADLESS_UA
        self._context = await pw.chromium.launch_persistent_context(
            self.user_data_dir, **launch_kwargs
        )
        # stealth：隐藏自动化特征（webdriver 等），降低被风控识别概率
        try:
            await self._context.add_init_script(injections.get("stealth"))
        except Exception as se:  # noqa: BLE001
            log.warning("stealth 注入失败（不影响引擎启动）: %s", se)
        await self._replay_saved_cookies()
        return self._context

    async def _replay_saved_cookies(self) -> None:
        """R-6.37g 拆自 _launch_context：启动后回注持久化 Cookie（跨重启保留登录态）。"""
        # 登录态重启保留（R-6.6）：以 data/cookies.json 为权威源，启动即回注。
        # 注意：回注必须读持久化文件（cookie_file property），绝不能读内存。
        try:
            saved = []
            cf = self.cookie_file
            if cf.exists():
                data = json.loads(cf.read_text())
                saved = data.get("cookies", data) if isinstance(data, dict) else data
            if saved:
                await self._context.add_cookies(saved)
                log.info("账号 %s 回注持久化 Cookie %d 条", self.account_id, len(saved))
            else:
                log.info("账号 %s 无持久化 Cookie 可回注", self.account_id)
        except Exception as re:  # noqa: BLE001
            log.warning("账号 %s 回注持久化 Cookie 失败: %s", self.account_id, re)

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

    def task_page_snapshot(self):
        """返回该账号缓存的【任务 tab】（未关闭时）；供设置开关联动 Tab2 显隐（R-6.19）。"""
        tp = self._task_page
        if tp is not None:
            try:
                if not tp.is_closed():
                    return tp
            except Exception:  # noqa: BLE001
                pass
        return None

    async def new_page(self):
        """取一个可用页面：优先复用该账号缓存的【任务 tab】（同 tab 导航覆盖，
        从机制上消除「反复开关 tab」的观感）；无缓存则**在独立采集窗口**新建，
        失败退化为前台新页。新建后缓存为该账号任务 tab。

        R-6.21 双窗口：任务页（采集执行页）开在**独立 CFT 窗口**（--app 应用模式
        独立窗口，无地址栏/标签条 + 「采集调试窗口」标题守护；同引擎同 profile ——
        登录态/Cookie 共享、CDP 可控），即「窗口二（采集，由设置开关
        collect_tab_visible 控制开/关）」。窗口一=APP/登录（壳窗口应用模式，
        keep_shell_tab 监护固定不可关）。
        """
        if self._task_page is not None:
            try:
                if not self._task_page.is_closed():
                    return self._task_page
            except Exception:  # noqa: BLE001
                pass
            self._task_page = None
        ctx = await self.context()
        # R-6.22g（用户约定）：未登录 / 风控需验证时**不创建独立采集调试窗口**——
        # 右窗只在登录有效时用于采集调试；未登录时登录引导统一由左 APP ForceLogin
        # 承接（避免右窗直接弹出小红书平台登录层造成困惑）。任务页退回引擎内新页
        # （无独立调试窗），采集照常失败并触发前端登录引导。
        blocked = await self._debug_window_blocked(ctx)
        if blocked:
            log.info("账号 %s 未登录/风控需验证——跳过独立采集调试窗口，任务页走引擎内新页", self.account_id)
            # R-6.22j 修复（用户实测反馈「点采集后被引入空白页/窗口消失」）：
            # 原实现把 ctx.pages[0]（壳窗 APP 窗口）整体 minimized——本意是收起
            # 调试窗，但壳窗恰是承载 ForceLogin/登录引导的窗口，被最小化后用户
            # 看不到登录引导，只剩最小化窗口/空白页，观感即「乱」。
            # 现在不再最小化任何窗口：壳窗保持可见承接登录引导；
            # 任务页在引擎内新建（不建独立调试窗），登录墙由左 APP ForceLogin 承接。
            p = await ctx.new_page()
            self._task_page = p
            return p
        p = await self._open_task_page_window(ctx)
        if p is not None:
            return p
        p = await ctx.new_page()
        # R-6.22：退化路径新窗口校准为右半屏（等宽并排不重叠）
        try:
            w2, h2, _, l2 = shell_window_geometry()
            cdp = await p.context.new_cdp_session(p)
            t = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": t["windowId"],
                 "bounds": {"left": l2, "top": 0, "width": w2, "height": h2,
                            "windowState": "normal"}},
            )
        except Exception:  # noqa: BLE001（校准失败不影响任务页可用性）
            pass
        self._task_page = p
        return p

    async def _debug_window_blocked(self, ctx) -> bool:
        """R-6.36e 拆自 new_page()：未登录/风控需验证时不开独立采集窗（判定失败保守阻塞）。"""
        try:
            cs = await self.read_cookies()
            has_ws = any(c.get("name") == "web_session" for c in cs)
            if not has_ws:
                return True
            if await _any_blocked_page(ctx):
                return True
            return False
        except Exception:  # noqa: BLE001（判定失败保守不开窗）
            return True

    async def _open_task_page_window(self, ctx):
        """R-6.36e 拆自 new_page()：登录有效时开独立采集调试窗；失败返回 None（退化由调用方处理）。"""
        try:
            from src import shell_window  # noqa: PLC0415（规避模块级循环依赖）

            # R-6.52 修复：不再对壳窗（ctx.pages[0]）执行 setWindowBounds。
            # 原实现把壳窗窗口 bounds 设到右半屏(l2)，随后 open_tab2_window 又把
            # 采集窗弹到右半——两窗重叠，开始采集瞬间采集窗(小红书页)覆盖壳窗，
            # 用户观感「主窗瞬间跳小红书再切回」（主窗 URL 全程未变，纯窗口覆盖）。
            # 壳窗位置由 keeper 守护（左半），采集窗位置由 open_tab2_window 内部校准。
            src_page = ctx.pages[0]
            executable = None
            try:
                cfts = builtin_cft_candidates()
                if cfts:
                    executable = cfts
            except Exception:  # noqa: BLE001
                executable = None
            page = await shell_window.open_tab2_window(
                ctx, src_page, url="https://www.xiaohongshu.com/explore",
                user_data_dir=self.user_data_dir, executable=executable,
            )
            self._task_page = page
            return page
        except Exception as e:  # noqa: BLE001（窗口创建失败：退化为前台新页）
            log.warning("独立采集窗口创建失败，退化为前台新页: %r", e)
            return None

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
        R-6.22j：壳窗（shell_ref 引用）**无条件保护**——壳窗可能正被守卫导航到
        登录页/搜索页（URL 不含 keep_url），URL 匹配会误关壳窗（用户实测「点采集后
        被引入空白页」根因）。以引用判活优先，URL 匹配仅作补充。
        """
        if not keep_url:
            keep_url = f"127.0.0.1:{config.front_port()}"
        shell_pg = self._resolve_shell_protected_page()
        try:
            ctx = await self.context()
            closed = await self._close_unrelated_pages(ctx, shell_pg, keep_url)
            log.info("cleanup_tabs: closed=%d shell_protected=%s pages=%d",
                     closed, shell_pg is not None, len(ctx.pages))
            if not ctx.pages:  # 全部清空 → 补空白页保底
                try:
                    await ctx.new_page()
                except Exception:  # noqa: BLE001
                    pass
            return closed
        except Exception as exc:  # noqa: BLE001
            log.warning("账号 %s cleanup_tabs 失败（非阻断）: %s", self.account_id, exc)
            return 0

    def _resolve_shell_protected_page(self):
        """R-6.36e 拆自 cleanup_tabs()：壳窗保护引用（引用不可用返回 None，退回 URL 匹配）。"""
        try:
            from src import shell_window  # noqa: PLC0415

            shell_pg = shell_window.current_shell_page()
            if shell_pg is not None and shell_pg.is_closed():
                return None
            return shell_pg
        except Exception:  # noqa: BLE001
            return None

    async def _close_if_unrelated(self, p, shell_pg, keep_url) -> int:
        """R-6.37i 拆自 _close_unrelated_pages：单页无关则关闭，返回是否关闭（0/1）。"""
        u = p.url or ""
        if p is shell_pg or keep_url in u or u == "about:blank" or "xiaohongshu.com" in u:
            return 0
        try:
            await p.close()
            if self._task_page == p:
                self._task_page = None
            return 1
        except Exception:  # noqa: BLE001
            return 0

    async def _close_unrelated_pages(self, ctx, shell_pg, keep_url) -> int:
        """R-6.36e 拆自 cleanup_tabs()：遍历关闭无关 tab（壳窗/APP 页/空白/小红书 tab 保留）。"""
        closed = 0
        for p in ctx.pages:
            closed += await self._close_if_unrelated(p, shell_pg, keep_url)
        return closed

    async def collapse_debug_windows(self) -> int:
        """R-6.22g：登录引导时收起采集调试窗——关闭非壳窗口页面，壳窗（APP/登录页）保留。

        触发时机：ForceLogin 弹出（前端调用）/ 点「去登录」（login_begin 后端调用）。
        登录完成后用户再次点采集，new_page 按登录态恢复调试窗（登录有效恢复右半屏）。
        """
        return await self.cleanup_tabs()

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
