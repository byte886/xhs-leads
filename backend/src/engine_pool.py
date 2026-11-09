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
from src.cdp_timeouts import CDP_QUICK, cdp_call
from src.engine_types import builtin_cft_candidates, builtin_extension_dir

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

# 窗口最小尺寸：低于这个尺寸前端上下布局也会挤成一团（640×480 是桌面应用常见可用下限，
# 也与 Material 3 Medium 档位下限呼应）。用于窗口放置时的钳制，防止被拖到不可用尺寸。
MIN_WIN_W = 640
MIN_WIN_H = 480

def shell_window_geometry() -> tuple[int, int, int, int]:
    """双窗口并排布局（R-6.22，R-6.80 高度改为按屏幕自适应）。

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


def account_window_geometry(account_id: str) -> tuple[int, int, int, int] | None:
    """非 default 账号的有头窗口位置（R-6.57 多账号错开）。

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

# R-6.19：采集窗（采集骨架）创建临界区锁——keeper 兜底轮询与 server 开关联动
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
    """exit_type 非 Normal 时修正为 Normal；返回是否有改动。R-6.49b。"""
    changed = False
    if prefs.get("exit_type") not in (None, "Normal"):
        prefs["exit_type"] = "Normal"
        changed = True
    prof = prefs.get("profile")
    if isinstance(prof, dict) and prof.get("exit_type") not in (None, "Normal"):
        prof["exit_type"] = "Normal"
        changed = True
    return changed


def _normalize_exit_type(default_dir: str) -> None:
    """把 Default/Preferences 的 exit_type（顶层 + profile 内）修正为 Normal。"""
    prefs_path = os.path.join(default_dir, "Preferences")
    try:
        if not os.path.isfile(prefs_path):
            return
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        if not _fix_exit_type(prefs):
            return
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
    except (OSError, ValueError):
        pass


def _collect_win_executable() -> str | None:
    """采集调试窗命令行路由用的内置 CFT 路径；拿不到就 None（退 window.open）。

    R-6.83：右窗要标准浏览器外观（地址栏/标签栏），只能靠命令行 `--new-window`
    路由，因此建窗时必须带上可执行路径。
    """
    try:
        return builtin_cft_candidates()
    except Exception:  # noqa: BLE001（打包/权限异常不该阻断建窗）
        return None


def _clear_session_restore(user_data_dir: str) -> None:
    """启动前清理 Chrome 会话恢复（R-6.48s / R-6.49b）。

    上次异常退出（exit_type=Crashed，如 pkill 停实例）时 Chrome 默认恢复上次全部
    tab（可能停在 xiaohongshu.com 登录页/首页）并弹「要恢复页面吗？Chromium未正确
    关闭」横幅 → 用户反馈「启动看到小红书一闪而过」。故启动前一次性清掉会话恢复
    三件套，并把 exit_type 修正为 Normal。不动 Cookies/DB——登录态由
    _replay_saved_cookies 以 cookies.json 为权威源回注，跨重启保留不受影响（R-6.6）。
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
        # R-6.57：每账号独占 CDP 端口（default=9223，其余 9224 起）。
        # 共用端口时 Chromium 绑定失败静默跳过 → 测试连到的永远是先起的账号。
        self.debug_port = config.account_debug_port(account_id)
        self._context = None
        self._lock = asyncio.Lock()
        self._task_page = None  # 该账号唯一任务 tab（复用，减少反复开关 tab）
        self._rebuild_ts = 0.0  # R-6.53b：最近一次引擎重建时间戳（0=从未重建）
        self._fake_dead = False  # R-6.53l：CDP 假死标记（保持级：标记不立即释放）

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

    def mark_fake_dead(self) -> None:
        """R-6.53l：CDP 假死标记（保持级，用户拍板分级策略）——只标记不释放。

        假死（进程存活、协议无响应）可能自愈：立即释放会误杀可恢复实例并引入
        重建成本。保持引用，待下次真实动作（context() 调用）先恢复管理
        （_try_recover_cdp），无法恢复才优雅→强杀重建。"""
        self._fake_dead = True

    async def _try_recover_cdp(self) -> bool:
        """R-6.53l：假死恢复管理——快速探测 CDP 是否恢复（3s 阈值，远小于正常量级）。

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

    async def force_release(self) -> None:
        """CDP 假死强制释放（对外入口）：无条件关闭当前 context 并置空，下次 context() 重建。

        R-6.53l（用户拍板分级）：假死 → 保持（mark_fake_dead）→ 新动作先恢复管理
        （_try_recover_cdp）→ 无法恢复才释放（本方法）。login_status 超时只标记保持，
        由下次 context() 先尝试恢复，失败才走 _release_locked。
        R-6.53k：假死时 close() 挂起/失败（CDP 无响应）——5s 超时，失败后强杀残留进程
        （此前 close 异常忽略 → 实例残留，每轮假死积累实例+页面，renderer 膨胀内存消干）。
        """
        async with self._lock:
            self._fake_dead = False
            await self._release_locked(reason="假死无法恢复")

    async def _release_locked(self, reason: str) -> None:
        """R-6.53l 拆自 force_release：释放当前 context（调用方须已持有 _lock）。

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
        """R-6.53k：强杀本账号 profile 的残留 Chromium 进程（close 失败/假死兜底）。

        按 user_data_dir 精确匹配命令行，不影响其他账号与其他 Chrome。先 TERM 给
        优雅退出窗口（2s，能落盘登录态），残留再 KILL。仅在旧 context 已释放后调用
        （force_release / 断开重建 / 启动前），不会误杀新实例。同步阻塞 ~2s 可接受
        （内存回收优先于登录判定瞬时延迟）。
        """
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

    async def _context_fake_dead_step(self):
        """R-6.53l 假死保持级处置。

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

        已断开则释放旧引用（close 失败/超时即强杀残留，防实例堆积，R-6.53k），
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
        self._kill_stale_processes()  # R-6.53k：断开重建前清残留
        log.warning("账号 %s 引擎连接断开，自动重建", self.account_id)
        return False


    async def _context_start(self, pw):
        """启动引擎并审计。失败直接抛 RuntimeError 由上层处置（R-6.22j 无冷却）。"""
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
            # R-6.22j（用户拍板）：无「启动失败 20s 冷却」——失败直接抛错，
            # 由上层（任务 error / 用户重试）处置，避免误拦截与空白窗口观感。
            pw = await _get_pw()
            return await self._context_start(pw)


    def _launch_kwargs_base(self, headless: bool, eng_port: int) -> dict:
        """构造 launch_persistent_context 基础参数（channel/viewport/args）。

        R-6.27：非无头（壳窗口）时视口必须与窗口几何一致，否则 960 宽窗口显示
        1280 布局、右侧内容被裁；无头引擎固定采集布局。
        R-6.80：非无头改为「无视口模式」（Playwright 的 no_viewport=True，注意公开
        API 是 snake_case 的 no_viewport，不是 driver 层的 noViewport）。固定数值
        viewport 会把页面尺寸锁死，窗口被拖大/缩小后页面不跟随重排——这就是
        「APP 窗口不会自适应页面大小」的直接原因。另注意不能只传 viewport=None：
        Playwright 客户端会把值为 None 的字段当作「未传」丢掉，服务端随即套用默认
        1280x720（实测 innerWidth=1280 而 outerWidth=960），必须显式 no_viewport。
        R 批 v1.5：引擎带 CDP 端口供 UI 测试 connect_over_cdp 复用同一实例（同一登录态 profile）。
        R-6.57：端口由调用方传入该账号独占的 debug_port（default=9223，其余 9224 起）。
        """
        geo = shell_window_geometry()[:2]
        base = dict(
            channel="chrome",
            headless=headless,
            args=[
                # R-6.53j：移除 --no-first-run——Chromium 150+ 带该 flag 有
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
        """非无头时的窗口几何（R-6.21 双窗口 + R-6.49d 初始窗移出屏幕）。

        壳窗口应用模式由 shell_window.ensure_shell 走 --app 路由弹出；此处保留
        --window-size/--window-position 让启动初始普通窗口自生自灭：负坐标消除
        「普通→应用提升」时左半屏闪现（CDP 取证：启动期两个 8791 target，先普通后 --app）。
        """
        win_w, win_h, _, _ = shell_window_geometry()
        launch_kwargs["args"].append(f"--window-size={win_w},{win_h}")
        launch_kwargs["args"].append("--window-position=-4000,0")


    def _launch_apply_geolocation(self, launch_kwargs: dict) -> None:
        """R-6.81：注入固定坐标并授予 geolocation 权限。

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
        """R-6.53：--log-file 让 Chromium base::logging 崩溃前 FATAL 落盘。

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
        """选二进制（R-6.11 内置 CFT 优先）并处理 UA / Playwright 默认参数。

        CFT 独立应用身份（com.google.chrome.for.testing）→ 不再占用用户日常 Chrome
        的 LaunchServices 单实例（根除「APP 开着时日常 Chrome 无法新建窗口」）；
        缺失时回退 channel=chrome。CFT 为自动化专用 build，无 first-run 向导。
        """
        cft = builtin_cft_candidates()
        if cft:
            launch_kwargs["executable_path"] = cft
            launch_kwargs.pop("channel", None)
        log.info("引擎二进制: %s", cft or "channel=chrome（系统 Chrome）")
        if headless:
            launch_kwargs["user_agent"] = _HEADLESS_UA
        # R-6.53j：Playwright driver 默认 args 也含 --no-first-run，须显式忽略
        launch_kwargs["ignore_default_args"] = ["--no-first-run"]

    def _launch_apply_extension(self, launch_kwargs: dict) -> None:
        """R-6.70：可选自动加载未打包扩展（默认关闭，由 config.extension_enabled 控制）。

        仅当扩展目录含 manifest.json 时才追加两个 flag；目录缺失则什么都不做，
        启动行为与开关关闭时完全一致。不引入 daemon/WebSocket，也不改变进程模型。
        """
        ext_dir = builtin_extension_dir()
        if not ext_dir:
            log.info("扩展开关已开但未找到含 manifest 的扩展目录，跳过加载")
            return
        launch_kwargs["args"].append(f"--load-extension={ext_dir}")
        launch_kwargs["args"].append(f"--disable-extensions-except={ext_dir}")

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
        # default 跳过——它承载 APP 壳窗，标题由 shell_title 恒定守护（用户 R-6.48h 拍板）。
        if self.account_id != "default":
            for pg in list(self._context.pages)[:1]:
                await self._apply_account_title(pg)
        await self._replay_saved_cookies()


    async def _launch_context(self, pw):
        """构建并 launch_persistent_context（含 Cookie 回注/stealth），只做编排。"""
        os.makedirs(self.user_data_dir, exist_ok=True)
        # R-6.53k：启动前清该 profile 残留进程 —— 残留实例占 profile 锁导致
        # Failed to launch，且多实例页面堆积耗内存。
        self._kill_stale_processes()
        # R-6.48s：启动前清会话恢复文件，避免「启动先闪现小红书页再导航 APP」。
        _clear_session_restore(self.user_data_dir)
        headless = config.account_headless(self.account_id)
        launch_kwargs = self._launch_kwargs_base(headless, self.debug_port)
        if not headless:
            self._launch_apply_shell_window(launch_kwargs)
        self._launch_apply_chrome_log(launch_kwargs)
        self._launch_apply_binary(launch_kwargs, headless)
        self._launch_apply_geolocation(launch_kwargs)
        # R-6.84：采集窗标签分组（内置扩展，默认开）与 R-6.70 的按账号桥接开关并集。
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
        """R-6.57：非 default 账号的有头窗口按序号移到屏内可见位置。

        启动参数统一用 --window-position=-4000,0（消除「普通窗→应用窗」左半屏闪现），
        default 账号随后由 shell_window 校准；其余账号没有壳窗流程，必须在此自己定位，
        否则会永久停在屏幕外——用户看到的现象是「第二个账号起了但找不到窗口」。
        """
        geo = account_window_geometry(self.account_id)
        if geo is None or self._context is None:
            return
        if config.account_headless(self.account_id):
            return  # 无头模式没有窗口，定位无意义
        win_w, win_h, left, top = geo
        try:
            page = self._context.pages[0] if self._context.pages else None
            if page is None:
                return
            cdp = await self._context.new_cdp_session(page)
            info = await cdp.send("Browser.getWindowForTarget")
            wid = info.get("windowId")
            if wid is None:
                return
            target = {"left": left, "top": top, "width": win_w, "height": win_h}
            # 实测：启动瞬间窗口仍在校准，立刻 set 会被随后的默认摆放覆盖（回读仍是
            # 960,25,960x800）；必须等窗口稳定后再设，且回读确认才算成立。
            cur = {}
            for _ in range(3):
                await asyncio.sleep(0.7)
                await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": target})
                cur = (await cdp.send("Browser.getWindowBounds", {"windowId": wid})).get("bounds", {})
                if (cur.get("left"), cur.get("top")) == (left, top):
                    log.info("账号 %s 窗口定位 (%s,%s %sx%s)", self.account_id, left, top, win_w, win_h)
                    return
            log.warning("账号 %s 窗口定位未确认，回读=%s", self.account_id, cur)
        except Exception as e:  # noqa: BLE001（定位失败不影响引擎可用）
            log.warning("账号 %s 窗口定位失败（不影响启动）: %s", self.account_id, e)


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


    async def restore_window(self) -> bool:
        """恢复有头窗口到账号级联位置（带 bounds，实测比只给 windowState 可靠）。"""
        geo = account_window_geometry(self.account_id)
        if geo is None:
            return await self.set_window_state("normal")
        win_w, win_h, left, top = geo
        return await self.set_window_state(
            "normal", {"left": left, "top": top, "width": win_w, "height": win_h})


    async def _replay_saved_cookies(self) -> None:
        """R-6.37g 拆自 _launch_context：启动后回注持久化 Cookie（跨重启保留登录态）。

        R-6.72：原口径「以 cookies.json 为权威源、启动即回注」有个致命副作用——
        cookies.json 是**旧快照**，启动时无条件回注会把 profile 里浏览器自己
        持久化的**新**会话覆盖成旧值。实测表现：扫码登录后 profile 的 Cookies
        文件持续写盘（mtime 就是当下），但一重启页面就退回登录页，用户每次
        都要重扫。改为：profile 已有 web_session 就跳过回注，只在 profile 空
        （导入 cookie 到新账号这类场景）时兜底。
        """
        # 注意：回注必须读持久化文件（cookie_file property），绝不能读内存。
        try:
            cur = []
            if self._context is not None:
                cur = await self._context.cookies()
            if any(c.get("name") == "web_session" for c in cur):
                log.info("账号 %s profile 已有会话，跳过 Cookie 回注", self.account_id)
                return
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
        """返回该账号缓存的【任务 tab】（未关闭时）；供设置开关联动 采集窗 显隐（R-6.19）。"""
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
            await self._apply_account_title(p)
            self._task_page = p
            return p
        # R-6.53r：采集工作页改普通 tab（新 tab 前台激活、渲染完整、CDP 会话稳定）。
        # 转交窗（--app Popen 单实例路由）在 CFT 158 上实测两问题（Nov 5 取证）：
        # ① 后台化渲染节流——wait_cards n=0（同 URL probe 为 22 卡片）；
        # ② CDP 会话不稳——wait_cards 第二轮 evaluate 挂起触发假死强杀。
        # 独立采集调试窗（keeper 采集窗）保留为展示窗，采集执行页与之解耦。
        p = await ctx.new_page()
        await self._apply_account_title(p)
        # R-6.22：退化路径新窗口校准为右半屏（等宽并排不重叠）
        # R-6.57：多账号时不能用统一的右半屏——否则第二个账号的窗口会被摆回
        # default 采集窗的位置（实测启动后定位成功又被这里的 960,25 覆盖）。
        try:
            acc_geo = account_window_geometry(self.account_id)
            if acc_geo:
                w2, h2, l2, top2 = acc_geo
            else:
                w2, h2, _, l2 = shell_window_geometry()
                top2 = 0
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
        """账号窗口标题文字：一眼看出这个窗口是哪个号（R-6.69b 用户要求）。

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
            # 原实现把壳窗窗口 bounds 设到右半屏(l2)，随后 open_collect_window 又把
            # 采集窗弹到右半——两窗重叠，开始采集瞬间采集窗(小红书页)覆盖壳窗，
            # 用户观感「主窗瞬间跳小红书再切回」（主窗 URL 全程未变，纯窗口覆盖）。
            # 壳窗位置由 keeper 守护（左半），采集窗位置由 open_collect_window 内部校准。
            src_page = ctx.pages[0]
            page = await shell_window.open_collect_window(
                ctx, src_page, url="https://www.xiaohongshu.com/explore",
                user_data_dir=self.user_data_dir,
                executable=_collect_win_executable(),
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

    async def read_cookies(self, timeout: float = 10.0) -> list[dict]:
        """通过 CDP 读该账号引擎当前全部小红书 Cookie（只读，不关闭页面）。

        R-6.53i：CDP 假死防御——引擎进程存活但 DevTools 协议无响应时，
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
