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
import re
import subprocess
import sys
import time

from playwright.async_api import async_playwright

from src import config, injections, logger as xlog, window_layout
from src.cdp_timeouts import CDP_QUICK, cdp_call
from src.engine_types import builtin_cft_candidates, builtin_extension_dir

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
    if sys.platform == "win32":
        # Windows：GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)。先声明 DPI 感知，
        # 否则缩放 150% 的机器会拿到被系统缩放后的逻辑分辨率（窗口会偏小一半）。
        try:
            import ctypes  # noqa: PLC0415

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:  # noqa: BLE001（旧系统无 shcore）
                ctypes.windll.user32.SetProcessDPIAware()
            w = int(ctypes.windll.user32.GetSystemMetrics(0))
            h = int(ctypes.windll.user32.GetSystemMetrics(1))
        except Exception:  # noqa: BLE001（探测失败走回退）
            pass
        _SCREEN = (w, h)
        return _SCREEN
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

# 窗口最小尺寸：低于这个尺寸前端上下布局也会挤成一团（640×480 是桌面应用常见可用下限，
# 也与 Material 3 Medium 档位下限呼应）。用于窗口放置时的钳制，防止被拖到不可用尺寸。
MIN_WIN_W = 640
MIN_WIN_H = 480

# 创建窗口时的初始坐标：先落在屏幕外，等 CDP 摆位完成再显示。
# 用于「壳窗提升 --app 窗口」与「采集调试窗」——避免新窗先在与壳窗重叠的左半屏
# 出现几百毫秒，被用户看成「左边窗口又打开了小红书 / 两个 APP 窗叠在一起」。
OFFSCREEN_LEFT = -4000

# 采集窗页的认领判据：只认小红书浏览站域（www.），创作平台等子域不算（见 _page_by_url）。

def shell_window_geometry() -> tuple[int, int, int, int]:
    """双窗口并排布局。

    主屏 SW×SH：壳窗口宽 = min(1280, SW // 2)，位置 (0, 0)（主屏左半）；
    采集调试窗口同宽，位置 (win_w, 0)（主屏右半）。两窗等宽并排、互不重叠。

    高度旧版写死 800：在 1200 高的屏上底部空 400，页面内容一屏装不下、需多滚。
    现在取 SH - 60（留菜单栏/程序坞余量），上限 1200（大屏不无限拉高），
    下限 MIN_WIN_H。宽度同样钳到 MIN_WIN_W，保证小屏机器也不会开出不可用窄窗。
    """
    sw, sh = probe_screen_size()
    win_w = min(1280, max(MIN_WIN_W, sw // 2))
    win_h = max(MIN_WIN_H, min(1200, sh - 60))
    return win_w, win_h, 0, win_w


def default_window_layout() -> dict[str, dict]:
    """首次启动（无历史记录）的默认双窗布局：壳窗左半、采集窗右半，等宽并排。"""
    win_w, win_h, shell_left, collect_left = shell_window_geometry()
    return {
        "shell": {"left": shell_left, "top": 0, "width": win_w, "height": win_h},
        "collect": {"left": collect_left, "top": 0, "width": win_w, "height": win_h},
    }


_LAYOUT_CACHE: dict[str, dict] | None = None


def window_bounds(role: str) -> dict:
    """该窗口本次启动的生效位置：上次退出前记录优先，否则默认分屏。

    只在启动期读一次并缓存：启动之后用户拖动窗口不应改变本次进程内的目标位置，
    否则后续任何一次「重建窗口」都会按拖动后的新位置重摆，产生漂移。
    """
    global _LAYOUT_CACHE
    if _LAYOUT_CACHE is None:
        sw, sh = probe_screen_size()
        default = default_window_layout()
        _LAYOUT_CACHE = {
            r: window_layout.bounds_for(r, default[r], sw, sh) for r in default
        }
    return dict(_LAYOUT_CACHE.get(role) or default_window_layout()[role])



def account_window_geometry(account_id: str) -> tuple[int, int, int, int] | None:
    """非 default 账号的有头窗口位置。

    default 账号仍走 shell_window_geometry（左壳窗 + 右采集窗）。其余账号从
    屏幕右下按序号级联偏移，保证每窗标题栏可见、可点击切换，不整窗压死在前一窗上。

    返回 (宽, 高, left, top)；default 返回 None（由 shell_window 管）。
    """
    if not account_id or account_id == "default":
        return None
    others = [a for a in config.list_accounts().keys() if a != "default"]
    idx = others.index(account_id) + 1 if account_id in others else 1
    sw, sh = probe_screen_size()
    win_w = min(1100, sw - 60)
    win_h = min(760, sh - 140)
    step = 36
    k = min(idx, 6) - 1
    left = max(0, sw - win_w - 20 - k * step)
    top = max(28, sh - win_h - 60 - k * step)
    return win_w, win_h, left, top

# 并发创建会双开 explore 骨架；共享同一锁串行化「检查+创建」。
COLLECT_WIN_GUARD_LOCK = asyncio.Lock()

log = xlog.get("engine")

_pw = None
_pw_lock: asyncio.Lock | None = None

# 无头模式 UA（R 批 v1.4）：Chrome 无头 UA 带 HeadlessChrome 特征（风控指纹），
# 无头启动时覆盖为有头 UA。版本随本机 Chrome 漂移：低版本 UA 仅指纹差异不影响可用性，
# 升级 Chrome 时顺手更新版本号即可。
_HEADLESS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
)

# Playwright 默认注入的「自动化专用」参数，2026-11-13 在 Win11 采集窗用进程命令行取证
# 确认其造成两类问题和现象一一对应：
# 1) 风控指纹：`--disable-backgrounding-occluded-windows` + `--disable-renderer-backgrounding`
#    让被遮挡的窗口仍满帧跑 rAF（实测采集窗被黑窗盖住时 1.2s 仍 73 帧）。真人浏览器窗口
#    被遮挡会降频/暂停，风控据此判定自动化，把人机验证页做成「僵尸页」——画面正常、渲染
#    在跑，但 CDP 合成点击、JS dispatchEvent、真实驱动级鼠标点击全都不响应。
# 2) 功能冲突：默认的 `--disable-extensions` 会压掉我们自己 `--load-extension` 的扩展，
#    表现为「打包版没有扩展注入、采集窗缺蓝色高亮」。
# 网页读不到自身命令行，但这些参数产生的行为特征可观测，故从默认参数里逐项摘掉。
# 只摘独立的、可精确匹配的项（Playwright 按完整字符串匹配，合并型
# `--disable-features=a,b,c` 摘不掉也不强摘），且都不影响单账号单标签的实际需求。
_IGNORE_DEFAULT_ARGS = [
    "--no-first-run",
    "--disable-extensions",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-popup-blocking",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-prompt-on-repost",
    "--disable-search-engine-choice-screen",
    "--disable-breakpad",
    "--allow-pre-commit-input",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--metrics-recording-only",
]

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
    """正文是否命中风控/需验证关键词。"""
    return any(k in txt for k in ("登录后查看", "风控拦截", "需人工扫码",
                                  "人工验证", "验证后重试", "登录超限",
                                  "请重新登录", "登录已过期"))




async def _any_blocked_page(ctx) -> bool:
    """任一小红书页正文命中风控关键词则 True。"""
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


def _purge_sessions_dir(default_dir: str) -> None:
    """删 Default/Sessions/* 下的会话恢复文件（不存在或删除失败均静默）。"""
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


def _purge_current_session_files(default_dir: str) -> None:
    """删 Current Session / Current Tabs（Chrome 记录「上次会话」的两个指针文件）。"""
    for name in _SESSION_RESTORE_FILES:
        try:
            p = os.path.join(default_dir, name)
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _fix_exit_type(prefs):
    """exit_type 非 Normal 时修正为 Normal；返回是否有改动。。"""
    changed = False
    if prefs.get("exit_type") not in (None, "Normal"):
        prefs["exit_type"] = "Normal"
        changed = True
    prof = prefs.get("profile")
    if isinstance(prof, dict) and prof.get("exit_type") not in (None, "Normal"):
        prof["exit_type"] = "Normal"
        changed = True
    return changed


def _drop_window_restore(prefs) -> bool:
    """删掉 Chrome 记下的「上次窗口摆放」，让命令行 --window-position 生效。。

    实测（2026-11-09 冷启动）：profile 里有 window placement 时 Chrome 按记录恢复
    窗口 bounds，**忽略 --window-position**——首个窗口仍在 (0,25) 完整显示
    about:blank 约 2.3s，这就是「启动瞬间壳窗区一片白」的直接来源。
    删掉这一项后，初始普通窗口直接按 --window-position 落在屏幕外。
    """
    browser = prefs.get("browser")
    if not isinstance(browser, dict) or "window_placement" not in browser:
        return False
    browser.pop("window_placement", None)
    return True


def _normalize_exit_type(default_dir: str) -> None:
    """把 Default/Preferences 的 exit_type（顶层 + profile 内）修正为 Normal，并清窗口记忆。"""
    prefs_path = os.path.join(default_dir, "Preferences")
    try:
        if not os.path.isfile(prefs_path):
            return
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        changed = _fix_exit_type(prefs)
        changed = _drop_window_restore(prefs) or changed
        if not changed:
            return
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
    except (OSError, ValueError):
        pass



def _clear_session_restore(user_data_dir: str) -> None:
    """启动前清理 Chrome 会话恢复。

    上次异常退出（exit_type=Crashed，如 pkill 停实例）时 Chrome 默认恢复上次全部
    tab（可能停在 xiaohongshu.com 登录页/首页）并弹「要恢复页面吗？Chromium未正确
    关闭」横幅 → 用户反馈「启动看到小红书一闪而过」。故启动前一次性清掉会话恢复
    三件套，并把 exit_type 修正为 Normal。不动 Cookies/DB——登录态就存在账号
    profile 里（Cookie 由浏览器自己写盘），跨重启天然保留，无需外部搬运。
    """
    default_dir = os.path.join(user_data_dir, "Default")
    _purge_sessions_dir(default_dir)
    _purge_current_session_files(default_dir)
    _normalize_exit_type(default_dir)


class AccountEngine:
    """单账号引擎：持久化上下文 + 生命周期 + 页面管理。"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        acc = config.get_account(account_id) or config.add_account(account_id)
        self.user_data_dir = config.resolve_account_dir(acc.get("user_data_dir", ""))
        self.role = acc.get("role", "mixed")
        # 共用端口时 Chromium 绑定失败静默跳过 → 测试连到的永远是先起的账号。
        self.debug_port = config.account_debug_port(account_id)
        self._context = None
        self._lock = asyncio.Lock()
        self._task_page = None  # 该账号唯一任务 tab（复用，减少反复开关 tab）
        self._rebuild_ts = 0.0
        self._fake_dead = False

    # ---------------- 生命周期 ----------------

    def rebuild_age(self) -> float:
        """距最近一次引擎重建的秒数（未重建过返回极大值，供 keeper 区分「引擎
        崩溃重建后壳窗缺失」与「用户主动关闭主窗」）。"""
        if self._rebuild_ts <= 0:
            return 1e18
        return time.monotonic() - self._rebuild_ts

    def is_connected_now(self) -> bool:
        """同步判断当前 context 的浏览器连接是否存活（close 事件回调中同步调用）。"""
        c = self._context
        try:
            return c is not None and c.browser is not None and c.browser.is_connected()
        except Exception:  # noqa: BLE001（连接检测异常视同断开）
            return False

    def browser_process_alive(self) -> bool | None:
        """浏览器进程还在不在：True/False；无法判定返回 None（非 Windows 或没装 psutil）。

        为什么需要它：用户把采集窗和主窗都关掉时，Chrome 是**正常退出**，Playwright
        连接随之断开——和「引擎崩溃」在 is_connected_now() 上表现一模一样。
        只看连接会把它当成崩溃、跳过退出，留下无窗口的空进程（用户只能在任务管理器
        里杀）。这里按 profile 命令行查进程，进程没了就说明是用户关窗而非崩溃。
        """
        if os.name != "nt":
            return None
        try:
            import psutil  # noqa: PLC0415（仅 Windows 需要）
        except Exception:  # noqa: BLE001
            return None
        needle = str(getattr(self, "user_data_dir", "") or "").lower()
        if not needle:
            return None
        try:
            for p in psutil.process_iter(["pid", "cmdline"]):
                cmd = " ".join(p.info["cmdline"] or []).lower()
                if needle in cmd:
                    return True
        except Exception:  # noqa: BLE001（枚举失败：无法判定）
            return None
        return False

    def mark_fake_dead(self) -> None:
        """CDP 假死标记（保持级，用户拍板分级策略）——只标记不释放。

        假死（进程存活、协议无响应）可能自愈：立即释放会误杀可恢复实例并引入
        重建成本。保持引用，待下次真实动作（context() 调用）先恢复管理
        （_try_recover_cdp），无法恢复才优雅→强杀重建。"""
        self._fake_dead = True

    async def _try_recover_cdp(self) -> bool:
        """假死恢复管理——快速探测 CDP 是否恢复（3s 阈值，远小于正常量级）。

        恢复成功（CDP 会话可用）→ 复用当前引擎，避免无谓重建；失败 → 由调用方
        走 _release_locked（优雅 close → 强杀残留）。调用方须已持有 _lock。"""
        c = self._context
        if c is None:
            return False
        try:
            p = c.pages[0] if c.pages else None
            if p is None:
                return False
            cdp = await cdp_call(c.new_cdp_session(p), timeout=CDP_QUICK,
                                 label="probe.new_cdp_session", skip=True)
            if cdp is None:
                return False
            if await cdp_call(cdp.send("Target.getTargets"), timeout=CDP_QUICK,
                              label="probe.get_targets", skip=True) is None:
                return False
            return True
        except Exception:  # noqa: BLE001（探测超时/失败：判定无法恢复）
            return False

    async def _release_locked(self, reason: str) -> None:
        """释放当前 context（调用方须已持有 _lock）。

        优雅 close（5s 超时，落盘登录态）→ 失败/超时强杀该 profile 残留进程；
        全程日志 + audit 提示（用户要求「所有这种情况需要给到提示」）。"""
        c = self._context
        self._context = None
        self._rebuild_ts = time.monotonic()
        if c is not None:
            try:
                await cdp_call(c.close(), timeout=5.0, label="pool.close", skip=True)
            except Exception:  # noqa: BLE001（假死 close 挂起/失败：超时后强杀）
                pass
        self._kill_stale_processes()
        log.warning("账号 %s 引擎 %s，已强制释放并清理残留进程（下次调用自动重建）", self.account_id, reason)
        xlog.audit(self.account_id, action="engine_force_release", ok=True, detail=reason)

    def _kill_stale_processes(self) -> None:
        """强杀本账号 profile 的残留 Chromium 进程（close 失败/假死兜底）。

        按 user_data_dir 精确匹配命令行，不影响其他账号与其他 Chrome。先 TERM 给
        优雅退出窗口（2s，能落盘登录态），残留再 KILL。仅在旧 context 已释放后调用
        （force_release / 断开重建 / 启动前），不会误杀新实例。同步阻塞 ~2s 可接受
        （内存回收优先于登录判定瞬时延迟）。
        """
        if sys.platform == "win32":
            self._kill_stale_processes_win()
            return
        try:
            out = subprocess.run(
                ["pgrep", "-f", str(self.user_data_dir)],
                capture_output=True, text=True, timeout=5,
            )
            pids = [p.strip() for p in out.stdout.split() if p.strip().isdigit()]
        except Exception:  # noqa: BLE001（pgrep 失败：跳过清理，交由启动失败路径暴露）
            return
        if not pids:
            return
        for pid in pids:
            try:
                os.kill(int(pid), 15)  # SIGTERM：优雅退出
            except ProcessLookupError:
                pass
        time.sleep(2.0)
        for pid in pids:
            try:
                os.kill(int(pid), 9)  # SIGKILL：残留兜底
            except ProcessLookupError:
                pass

    def _kill_stale_processes_win(self) -> None:
        """Windows 版残留清理：psutil 按命令行匹配本账号 profile，先 terminate 再 kill。"""
        try:
            import psutil  # noqa: PLC0415（仅 Windows 需要）

            needle = str(self.user_data_dir).lower()
            procs = []
            for p in psutil.process_iter(["pid", "cmdline"]):
                cmd = " ".join(p.info["cmdline"] or []).lower()
                if needle and needle in cmd:
                    procs.append(p)
        except Exception:  # noqa: BLE001（枚举失败：跳过清理，交由启动失败路径暴露）
            return
        if not procs:
            return
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
            except Exception:  # noqa: BLE001
                pass
        _gone, alive = psutil.wait_procs(procs, timeout=2.0)
        for p in alive:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    async def _context_fake_dead_step(self):
        """假死保持级处置。

        返回 'reuse'（已恢复，可复用当前引擎）/'rebuild'（无法恢复，已强制释放）/
        'skip'（无假死）。保持级语义：只标记不立即释放（可能自愈），下次真实动作
        先尝试恢复管理（3s 探测），避免误杀可恢复实例。
        """
        if not (self._fake_dead and self._context is not None):
            return "skip"
        if await self._try_recover_cdp():
            self._fake_dead = False
            log.warning("账号 %s 引擎 CDP 假死恢复，继续复用当前引擎", self.account_id)
            return "reuse"
        log.warning("账号 %s 引擎 CDP 假死无法恢复，进入强制释放重建", self.account_id)
        await self._release_locked(reason="假死无法恢复")
        self._fake_dead = False
        return "rebuild"


    async def _context_reuse_if_alive(self) -> bool:
        """当前 context 仍连接可用 → True（直接复用）。

        已断开则释放旧引用（close 失败/超时即强杀残留，防实例堆积，），
        并更新重建时间戳，返回 False 供调用方重新启动。
        """
        if self._context is None:
            return False
        try:
            if self._context.browser is not None and self._context.browser.is_connected():
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            await cdp_call(self._context.close(), timeout=5.0,
                           label="pool.context_close", skip=True)
        except Exception:  # noqa: BLE001（close 失败/超时：强杀残留，防实例堆积）
            pass
        self._context = None
        self._rebuild_ts = time.monotonic()
        self._kill_stale_processes()
        log.warning("账号 %s 引擎连接断开，自动重建", self.account_id)
        return False


    async def _context_start(self, pw):
        """启动引擎并审计。失败直接抛 RuntimeError 由上层处置。"""
        try:
            self._context = await self._launch_context(pw)
            xlog.audit(self.account_id, action="engine_start", ok=True,
                       detail=f"dir={config.home_relative(self.user_data_dir)}")
            return self._context
        except Exception as e:  # noqa: BLE001
            xlog.audit(self.account_id, action="engine_start", ok=False,
                       detail=f"启动失败: {str(e)[:200]}")
            raise RuntimeError(f"账号 {self.account_id} 引擎启动失败: {e}") from e


    async def context(self):
        """取（或懒启动）该账号的 persistent context。自愈：假死恢复、断开自动重建。"""
        async with self._lock:
            if await self._context_fake_dead_step() == "reuse":
                return self._context
            if await self._context_reuse_if_alive():
                return self._context
            # 由上层（任务 error / 用户重试）处置，避免误拦截与空白窗口观感。
            pw = await _get_pw()
            return await self._context_start(pw)


    def _launch_kwargs_base(self, headless: bool, eng_port: int) -> dict:
        """构造 launch_persistent_context 基础参数（channel/viewport/args）。

        非无头（壳窗口）时视口必须与窗口几何一致，否则 960 宽窗口显示
        1280 布局、右侧内容被裁；无头引擎固定采集布局。
        非无头改为「无视口模式」（Playwright 的 no_viewport=True，注意公开
        API 是 snake_case 的 no_viewport，不是 driver 层的 noViewport）。固定数值
        viewport 会把页面尺寸锁死，窗口被拖大/缩小后页面不跟随重排——这就是
        「APP 窗口不会自适应页面大小」的直接原因。另注意不能只传 viewport=None：
        Playwright 客户端会把值为 None 的字段当作「未传」丢掉，服务端随即套用默认
        1280x720（实测 innerWidth=1280 而 outerWidth=960），必须显式 no_viewport。
        引擎带 CDP 端口供 UI 测试 connect_over_cdp 复用同一实例（同一登录态 profile）。
        端口由调用方传入该账号独占的 debug_port（default=9223，其余 9224 起）。
        """
        geo = (window_bounds("shell")["width"], window_bounds("shell")["height"])
        base = dict(
            channel="chrome",
            headless=headless,
            args=[
                # CHECK/SIGTRAP 崩溃（Debian #1141618）；CFT 无 first-run 向导，移除无副作用。
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                f"--remote-debugging-port={eng_port}",
            ],
        )
        if headless:
            # 无头引擎不弹窗，固定采集布局
            base["viewport"] = {"width": geo[0], "height": geo[1]}
        else:
            # 有头：页面尺寸 = 真实窗口，随窗口缩放重排（no_viewport 关闭固定视口）
            base["no_viewport"] = True
        return base


    def _launch_apply_shell_window(self, launch_kwargs: dict) -> None:
        """非无头时的窗口几何。

        壳窗口应用模式由 shell_window.ensure_shell 走 --app 路由弹出；此处保留
        --window-size/--window-position 让启动初始普通窗口自生自灭：负坐标消除
        「普通→应用提升」时左半屏闪现（CDP 取证：启动期两个 8791 target，先普通后 --app）。
        """
        b = window_bounds("shell")
        launch_kwargs["args"].append(f"--window-size={b['width']},{b['height']}")
        launch_kwargs["args"].append(f"--window-position={OFFSCREEN_LEFT},0")


    def _launch_apply_geolocation(self, launch_kwargs: dict) -> None:
        """注入固定坐标并授予 geolocation 权限。

        平台「同城 / 附近」筛选会调用 navigator.geolocation。CFT 无 Google API key，
        真实定位必然失败（官方文档：geolocation features will not work），实测三档：
        denied → 立即 code:1（标签不生效）；granted 无坐标 → 6 秒后 code:3 超时后
        静默退回「不限」；granted + 注入 → 点「同城」进 active 态且结果切到本地内容。
        坐标按账号解析（config.account_geolocation），无头/有头同样注入。
        """
        geo_cfg = config.account_geolocation(self.account_id)
        launch_kwargs["geolocation"] = geo_cfg
        launch_kwargs["permissions"] = ["geolocation"]
        log.info(
            "账号 %s 注入定位 %.3f,%.3f（同城/附近筛选依赖）",
            self.account_id, geo_cfg["latitude"], geo_cfg["longitude"],
        )


    def _launch_apply_chrome_log(self, launch_kwargs: dict) -> None:
        """--log-file 让 Chromium base::logging 崩溃前 FATAL 落盘。

        此前无 stderr 捕获 + --disable-breakpad 关闭 Crashpad → .ips 拿不到 CHECK
        文案（atos 无 dSYM 死路）；每次启动截断文件，崩溃点即崩溃前最后内容。
        """
        try:
            chrome_log = config.DATA_DIR / "logs" / "chrome-engine.log"
            chrome_log.parent.mkdir(parents=True, exist_ok=True)
            chrome_log.write_text("")
            launch_kwargs["args"].append("--enable-logging=stderr")
            launch_kwargs["args"].append(f"--log-file={chrome_log}")
        except Exception:  # noqa: BLE001
            log.warning("引擎 CHECK 日志落盘配置失败（非阻断）")


    def _launch_apply_binary(self, launch_kwargs: dict, headless: bool) -> None:
        """选二进制并处理 UA / Playwright 默认参数。

        双端统一用 CFT：Mac 同 CFT 采集正常（2026-11-13 实测），且 CFT 独立身份
        （com.google.chrome.for.testing）不占用用户日常 Chrome 的单实例，产品独立分发
        也不能依赖用户机器预装 Chrome。Windows 侧的拦截经对照实验确认是「登录会话」
        被打标（同机游客态空 profile 正常、登录态被拦），不是 CFT 二进制问题，
        故清 profile 重扫即可，不换二进制。
        """
        cft = builtin_cft_candidates()
        if cft:
            launch_kwargs["executable_path"] = cft
            launch_kwargs.pop("channel", None)
        log.info("引擎二进制: %s", cft or "channel=chrome（系统 Chrome）")
        if headless:
            launch_kwargs["user_agent"] = _HEADLESS_UA
        launch_kwargs["ignore_default_args"] = _IGNORE_DEFAULT_ARGS

    def _launch_apply_extension(self, launch_kwargs: dict) -> None:
        """可选自动加载未打包扩展（默认关闭，由 config.extension_enabled 控制）。

        仅当扩展目录含 manifest.json 时才追加 flag；目录缺失则什么都不做，
        启动行为与开关关闭时完全一致。不引入 daemon/WebSocket，也不改变进程模型。

        Chromium 137+ 默认启用 `DisableLoadExtensionCommandLineSwitch`
        feature，命令行 `--load-extension` 会被静默忽略（表现为 Chrome 里扩展
        条目为 0、service worker 永不出现，而启动参数看起来完全正确）。必须显式
        关掉这个 feature 才能让未打包扩展被加载。
        """
        ext_dir = builtin_extension_dir()
        if not ext_dir:
            log.info("扩展开关已开但未找到含 manifest 的扩展目录，跳过加载")
            return
        launch_kwargs["args"].append(f"--load-extension={ext_dir}")
        launch_kwargs["args"].append(f"--disable-extensions-except={ext_dir}")
        launch_kwargs["args"].append(
            "--disable-features=DisableLoadExtensionCommandLineSwitch"
        )

    async def _launch_after_start(self) -> None:
        """启动后注入 stealth（R 批 v1.4 降低风控识别）并回注持久化 Cookie。"""
        try:
            await self._context.add_init_script(injections.get("stealth"))
        except Exception as se:  # noqa: BLE001
            log.warning("stealth 注入失败（不影响引擎启动）: %s", se)
        # 登录框遮罩锁：复用 shell_window 同款 force_login（隐藏关闭按钮 + 拦截
        # .reds-mask 点击与 ESC），防止扫码时点空白误关登录框。第三类账号引擎
        # 此前只注 stealth、缺这层锁，是真机点空白登录框消失的根因。
        try:
            await self._context.add_init_script(injections.get("force_login"))
        except Exception as fe:  # noqa: BLE001
            log.warning("force_login 注入失败（不影响引擎启动）: %s", fe)
        # 窗口标题带账号身份：刚开窗时还没有任务页，初始页就是用户看到的那个窗口，
        # 不在这里注入的话，标题会一直是平台的「小红书 - 你的生活兴趣社区」。
        if self.account_id != "default":
            for pg in list(self._context.pages)[:1]:
                await self._apply_account_title(pg)


    async def _launch_context(self, pw):
        """构建并 launch_persistent_context（含 Cookie 回注/stealth），只做编排。"""
        os.makedirs(self.user_data_dir, exist_ok=True)
        # Failed to launch，且多实例页面堆积耗内存。
        self._kill_stale_processes()
        _clear_session_restore(self.user_data_dir)
        headless = config.account_headless(self.account_id)
        launch_kwargs = self._launch_kwargs_base(headless, self.debug_port)
        if not headless:
            self._launch_apply_shell_window(launch_kwargs)
        self._launch_apply_chrome_log(launch_kwargs)
        self._launch_apply_binary(launch_kwargs, headless)
        self._launch_apply_geolocation(launch_kwargs)
        # 两者都只是追加 --load-extension；扩展目录缺失时 _launch_apply_extension 跳过。
        if config.extension_enabled(self.account_id) or config.collect_group_enabled():
            self._launch_apply_extension(launch_kwargs)
        self._context = await pw.chromium.launch_persistent_context(
            self.user_data_dir, **launch_kwargs
        )
        await self._launch_after_start()
        await self._place_account_window()
        return self._context


    async def _place_account_window(self) -> None:
        """+有头窗口启动后的初始定位。

        非 default 账号：按序号级联到屏内可见位置（否则永久停在屏幕外，用户看不到）。
        default（壳窗）账号：先挪到屏幕外——壳窗最终由 shell_window 用 `--app` 窗口
        在记录位置重建，初始普通窗口若留在左半屏，会和提升出来的新窗重叠闪现
        （实测冷启动 +11.8s~+12.3s 两个 8791 窗口同时在 left=0）。
        """
        if self._context is None or config.account_headless(self.account_id):
            return
        geo = account_window_geometry(self.account_id)
        if geo is None:
            b = window_bounds("shell")
            geo = (b["width"], b["height"], OFFSCREEN_LEFT, 0)
        win_w, win_h, left, top = geo
        await self._apply_window_bounds(win_w, win_h, left, top)

    @staticmethod
    def _bounds_landed(cur: dict, left: int, top: int) -> bool:
        """回读坐标是否算「已到位」。

        离屏目标不可能精确达成（macOS 会把窗口钳到至少 40px 可见），故 left<0 的
        目标只要左边缘确实出了屏幕就算到位——否则每次启动都会刷「定位未确认」告警。
        """
        if (cur.get("left"), cur.get("top")) == (left, top):
            return True
        return left < 0 and (cur.get("left") or 0) < 0

    async def _apply_window_bounds(self, win_w: int, win_h: int, left: int, top: int) -> bool:
        """把首窗摆到指定 bounds，回读坐标确认（不确认只告警，不阻断启动）。

        第一次立即 set：窗口此刻可能正显示 about:blank 白屏，越早挪走越好；实测偶尔
        仍会被随后的默认摆放覆盖，故保留后两次 0.7s 间隔的重试。
        """
        cdp, wid = await self._window_cdp()
        if cdp is None or wid is None:
            return False
        target = {"left": left, "top": top, "width": win_w, "height": win_h}
        cur: dict = {}
        try:
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(0.7)
                await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": target})
                raw = await cdp.send("Browser.getWindowBounds", {"windowId": wid})
                cur = raw.get("bounds", {})
                if self._bounds_landed(cur, left, top):
                    log.info("账号 %s 窗口定位 (%s,%s %sx%s)", self.account_id,
                             cur.get("left"), cur.get("top"), win_w, win_h)
                    return True
        except Exception as e:  # noqa: BLE001（定位失败不影响引擎可用）
            log.warning("账号 %s 窗口定位失败（不影响启动）: %s", self.account_id, e)
            return False
        log.warning("账号 %s 窗口定位未确认，回读=%s", self.account_id, cur)
        return False


    async def _window_cdp(self):
        """取窗口级 CDP 会话与 windowId（无 context / 无页面时返回 None, None）。"""
        if self._context is None:
            return None, None
        page = self._context.pages[0] if self._context.pages else None
        if page is None:
            return None, None
        cdp = await self._context.new_cdp_session(page)
        info = await cdp.send("Browser.getWindowForTarget")
        return cdp, info.get("windowId")


    async def set_window_state(self, state: str, bounds: dict | None = None) -> bool:
        """切换窗口形态（minimized / normal），不重启、不丢 page，回读确认。

        与 set_window（切无头，要重启引擎、20-40s、page 失效）的区别：这是同一个
        有头引擎内的窗口状态，0.5 秒级生效，CDP 与页面全程保持。无头账号无窗口，返回 False。
        """
        if config.account_headless(self.account_id):
            return False
        cdp, wid = await self._window_cdp()
        if cdp is None or wid is None:
            return False
        b = {"windowState": state}
        if bounds:
            b.update(bounds)
        await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})
        # 实测：发完立刻回读可能仍是旧状态（probe1 时序），等一下再确认
        for _ in range(3):
            await asyncio.sleep(0.5)
            cur = (await cdp.send("Browser.getWindowBounds", {"windowId": wid})).get("bounds", {})
            if cur.get("windowState") == state:
                return True
        return False


    async def minimize_window(self) -> bool:
        """最小化收起：瞬时、不重启、不打断任务。"""
        return await self.set_window_state("minimized")




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

    async def _reusable_collect_page(self, ctx):
        """可复用的采集窗页（同 context、未关闭），否则 None。

        两级判定：先认协调器登记的采集窗引用（keeper 建窗时写入），取不到再按 URL
        在 context 里认领——**不按标题**：两份 title 守护（采集调试窗口 / 账号运营窗口）
        会互相覆盖，标题实测会被改成「账号名 · 运营窗口」，据此认领会漏。
        跨账号（context 不同）一律不复用，避免 A 账号的采集跑进 B 账号窗口。
        """
        page = await self._registered_collect_page(ctx)
        return page if page is not None else await self._page_by_url(ctx)

    async def _registered_collect_page(self, ctx):
        """协调器登记的那一页——同 context 且未关闭才算数。"""
        try:
            from src import shell_window  # noqa: PLC0415（规避模块级循环依赖）

            page = shell_window.current_collect_page()
        except Exception:  # noqa: BLE001（协调器未就绪：退到标题认领）
            return None
        try:
            if not page.is_closed() and getattr(page, "context", None) == ctx:
                return page
        except Exception:  # noqa: BLE001
            return None
        return None

    async def _page_by_url(self, ctx):
        """在 context 内按 URL 认领采集窗页：落在小红书**浏览站**的页才算采集窗页。

        只认 www.xiaohongshu.com：创作服务平台（creator.*）与其它子域共用同一批
        Cookie 但信息架构不同（没有首页搜索框），认领过去会让采集在创作平台页上
        找搜索框、必然落空。
        """
        for pg in ctx.pages:
            try:
                if "www.xiaohongshu.com" in (pg.url or ""):
                    return pg
            except Exception:  # noqa: BLE001（页在导航/已关：跳过）
                continue
        return None

    async def new_page(self):
        """取一个可用页面：优先复用该账号缓存的【任务 tab】（同 tab 导航覆盖，
        从机制上消除「反复开关 tab」的观感）；无缓存则**在独立采集窗口**新建，
        失败退化为前台新页。新建后缓存为该账号任务 tab。

        双窗口：任务页（采集执行页）开在**独立 CFT 窗口**（--app 应用模式
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
        # 同一窗口里多一个组外标签会破坏「采集窗口」组的完整性，也让窗口标题在
        # 「采集调试窗口」与「运营窗口」之间来回跳。复用失败（未登记/已关/跨账号）走原路径。
        reused = await self._reusable_collect_page(ctx)
        if reused is not None:
            self._task_page = reused
            log.info("账号 %s 复用采集窗页作为任务页（单标签）", self.account_id)
            return reused
        # 右窗只在登录有效时用于采集调试；未登录时登录引导统一由左 APP ForceLogin
        # 承接（避免右窗直接弹出小红书平台登录层造成困惑）。任务页退回引擎内新页
        # （无独立调试窗），采集照常失败并触发前端登录引导。
        blocked = await self._debug_window_blocked(ctx)
        if blocked:
            log.info("账号 %s 未登录/风控需验证——跳过独立采集调试窗口，任务页走引擎内新页", self.account_id)
            # 原实现把 ctx.pages[0]（壳窗 APP 窗口）整体 minimized——本意是收起
            # 调试窗，但壳窗恰是承载 ForceLogin/登录引导的窗口，被最小化后用户
            # 看不到登录引导，只剩最小化窗口/空白页，观感即「乱」。
            # 现在不再最小化任何窗口：壳窗保持可见承接登录引导；
            # 任务页在引擎内新建（不建独立调试窗），登录墙由左 APP ForceLogin 承接。
            p = await ctx.new_page()
            await self._apply_account_title(p)
            self._task_page = p
            return p
        # 转交窗（--app Popen 单实例路由）在 CFT 158 上实测两问题（Nov 5 取证）：
        # ① 后台化渲染节流——wait_cards n=0（同 URL probe 为 22 卡片）；
        # ② CDP 会话不稳——wait_cards 第二轮 evaluate 挂起触发假死强杀。
        # 独立采集调试窗（keeper 采集窗）保留为展示窗，采集执行页与之解耦。
        p = await ctx.new_page()
        await self._apply_account_title(p)
        # default 采集窗的位置（实测启动后定位成功又被这里的 960,25 覆盖）。
        try:
            acc_geo = account_window_geometry(self.account_id)
            if acc_geo:
                w2, h2, l2, top2 = acc_geo
            else:
                b2 = window_bounds("collect")
                w2, h2, l2, top2 = b2["width"], b2["height"], b2["left"], b2["top"]
            cdp = await p.context.new_cdp_session(p)
            t = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": t["windowId"],
                 "bounds": {"left": l2, "top": top2, "width": w2, "height": h2,
                            "windowState": "normal"}},
            )
        except Exception:  # noqa: BLE001（校准失败不影响任务页可用性）
            pass
        self._task_page = p
        return p

    def _account_window_label(self) -> str:
        """账号窗口标题文字：一眼看出这个窗口是哪个号。

        昵称会改也不唯一，所以带上小红书号才能核对「这到底是哪个号」；
        未登录账号没有昵称/号，自动退到内部 id（acc_xxxx 这类唯一符）。
        """
        name = config.account_display_name(self.account_id)
        red = config.account_red_id(self.account_id)
        suffix = f" (@{red})" if red and name != f"@{red}" else ""
        return f"{name}{suffix} · 运营窗口"

    async def _apply_account_title(self, page) -> None:
        """给账号窗口挂标题守护（幂等；失败只告警不阻断开窗）。

        为什么必须做：多个运营账号窗口并排时，用户只能靠内容判断是哪个号，
        而内容页长得都差不多。标题栏是唯一稳定的身份标识。
        """
        try:
            js = injections.account_window_title_js(self._account_window_label())
            if not getattr(page, "_acct_title_installed", False):
                await page.add_init_script(js)  # 对后续导航自动生效
                page._acct_title_installed = True
            await page.evaluate(js)  # 当前文档立即生效
        except Exception as te:  # noqa: BLE001（标题美化失败不影响窗口可用）
            log.warning("账号 %s 窗口标题守护注入失败: %s", self.account_id, te)

    async def refresh_window_title(self) -> None:
        """身份（昵称/小红书号）刚落盘时，让已开的窗口标题立刻跟上。

        不这么做的话标题只在开窗那一刻算一次：登录前是内部 id，登录后还是内部 id，
        必须重开窗口才更新——用户要的正是「窗口上能看出这是哪个号」。
        """
        try:
            ctx = await self.context()
            for pg in list(ctx.pages):
                await self._apply_account_title(pg)
        except Exception as te:  # noqa: BLE001（标题刷新失败不影响业务）
            log.warning("账号 %s 刷新窗口标题失败: %s", self.account_id, te)

    async def _debug_window_blocked(self, ctx) -> bool:
        """未登录/风控需验证时不开独立采集窗（判定失败保守阻塞）。"""
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

    async def close_page_safe(self, page):
        """任务收尾：任务 tab【保留不关闭】（浏览器内留着，供下次任务复用导航 /
        登录墙页供用户扫码），仅兜底保证至少留一个空白页（防整窗退出反复开关）。

        引擎窗口本身常驻：不在任务级反复开-关 Chrome 进程（现象 1 机制核心）。
        真正清理无关 tab 走 cleanup_tabs()（如 APP 启动清理）。

        **壳窗页永不关闭**。用户口径是「APP 页面永远不会消失」；这里用与
        cleanup_tabs 同一套壳窗引用判定，任何路径误传壳窗页都跳过（引用不可用时按 URL 兜底）。
        """
        if page is self._resolve_shell_protected_page():
            log.info("跳过关闭壳窗页（APP 页面必须常驻）")
            return
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
        壳窗（shell_ref 引用）**无条件保护**——壳窗可能正被守卫导航到
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
        """壳窗保护引用（引用不可用返回 None，退回 URL 匹配）。"""
        try:
            from src import shell_window  # noqa: PLC0415

            shell_pg = shell_window.current_shell_page()
            if shell_pg is not None and shell_pg.is_closed():
                return None
            return shell_pg
        except Exception:  # noqa: BLE001
            return None

    async def _close_if_unrelated(self, p, shell_pg, keep_url) -> int:
        """单页无关则关闭，返回是否关闭（0/1）。"""
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
        """遍历关闭无关 tab（壳窗/APP 页/空白/小红书 tab 保留）。"""
        closed = 0
        for p in ctx.pages:
            closed += await self._close_if_unrelated(p, shell_pg, keep_url)
        return closed

    # ---------------- Cookie 辅助（按账号落盘） ----------------

    async def read_cookies(self, timeout: float = 10.0) -> list[dict]:
        """通过 CDP 读该账号引擎当前全部小红书 Cookie（只读，不关闭页面）。

        CDP 假死防御——引擎进程存活但 DevTools 协议无响应时，
        Playwright 内部 timeout 依赖 CDP 响应不触发，任务会无限挂起；
        外层 wait_for 超时保证调用链不被卡死（超时抛 asyncio.TimeoutError，由上层处置）。
        """
        async def _impl() -> list[dict]:
            ctx = await self.context()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            cdp = await ctx.new_cdp_session(page)
            data = await cdp.send("Network.getAllCookies")
            return [
                c for c in data.get("cookies", [])
                if "xiaohongshu" in c.get("domain", "") or "xhscdn" in c.get("domain", "")
            ]
        return await asyncio.wait_for(_impl(), timeout=timeout)



class EnginePool:
    """账号 → AccountEngine 注册表（全局单例 pool）。"""

    VISIBLE_LIMIT = 3  # 屏幕上同时可见（normal）的有头窗口上限，超出自动最小化最旧的

    def __init__(self):
        self._engines: dict[str, AccountEngine] = {}
        self._visible_order: list[str] = []  # 可见窗口 LRU，末尾是最近可见
        self._lock = asyncio.Lock()

    def mark_visible(self, account_id: str) -> None:
        """记录某账号窗口变为可见（normal），并提到 LRU 末尾。"""
        if account_id in self._visible_order:
            self._visible_order.remove(account_id)
        self._visible_order.append(account_id)

    def mark_hidden(self, account_id: str) -> None:
        """记录某账号窗口被最小化/关闭，从可见顺序移除。"""
        if account_id in self._visible_order:
            self._visible_order.remove(account_id)

    def visible_order(self) -> list[str]:
        """当前可见窗口顺序（旧→新；单测与诊断读）。"""
        return list(self._visible_order)

    async def enforce_visible_cap(self, account_id: str,
                                  limit: int | None = None) -> list[str]:
        """开窗前调用：保证可见窗口不超过 limit（默认 VISIBLE_LIMIT）。

        account_id 是即将可见的账号（先不计入）；其余可见账号若已达上限，
        把最旧的逐个最小化，直到给 account_id 腾出名额。返回被收起的账号。
        """
        if limit is None:
            limit = self.VISIBLE_LIMIT
        others = [a for a in self._visible_order if a != account_id]
        hidden = []
        while len(others) >= limit:  # account_id 要占一个，others 最多留 limit-1
            old = others.pop(0)
            eng = self._engines.get(old)
            if eng is not None and await eng.minimize_window():
                hidden.append(old)
                if old in self._visible_order:
                    self._visible_order.remove(old)
        return hidden


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
