"""引擎窗口控制（原名 mirror.py，按职责改名）。

历史与现状：
- R 批 v1.4 的设计是「截图流 + 事件转发」模拟远程桌面（左窗内嵌引擎截图、鼠标事件回传）。
-用户改口径为「**实时引擎窗口**」——引擎开成有头小窗，人直接看真窗口操作。
  旧的截图映射与事件转发随之失去意义，已于删除，避免留孤儿代码。

现有能力：
- open_login(account)：确保引擎里有一个小红书页（无则开 explore，未登录自动弹登录 modal）；供起引擎的脚本调用
- set_window(account, on)：调试实时窗口开关（有头小窗 ↔ 无头；引擎重启生效，登录态回注保留）

注意：登录页不再由本模块"驱动"——第二窗口（采集窗）自主打开与刷新，此处只保证页面存在。
"""
import asyncio

from src import engine_pool, logger as xlog

log = xlog.get("mirror")

XHS_EXPLORE = "https://www.xiaohongshu.com/explore"
# 浏览页模式——未登录平台自动弹同款 login-modal，背后有浏览页、侧边栏登录按钮
# 常驻可重新唤起，配合 force_login 遮罩锁避免点空白丢失。常量保留供兼容/回退。
XHS_LOGIN = "https://www.xiaohongshu.com/login"


async def login_page(account: str = "default"):
    """取镜像目标页：引擎活动页中的小红书页；无则新建并打开 explore（未登录自动
    弹登录 modal；已登录平台直接给浏览流）。"""
    eng = await engine_pool.pool.get(account)
    ctx = await eng.context()
    for p in ctx.pages:
        try:
            if not p.is_closed() and (p.url or "").startswith("http") and "xiaohongshu" in p.url:
                return p
        except Exception:  # noqa: BLE001
            continue
    page = await eng.new_page()
    await page.goto(XHS_EXPLORE, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)  # 等 SPA 登录弹窗/浏览流渲染稳定
    return page


async def open_login(account: str = "default") -> dict:
    """登录小窗打开：确保引擎打开小红书登录页（截图流/事件转发的目标页就绪）。"""
    from src import config

    await login_page(account)
    # default 是固定壳窗/采集窗，不参与第三类账号的「最多 3 个」管理
    if account != "default" and not config.account_headless(account):
        made_room = await engine_pool.pool.enforce_visible_cap(account)
        engine_pool.pool.mark_visible(account)
        return {"ok": True, "account": account, "made_room": made_room}
    return {"ok": True, "account": account}


async def scan_login(account: str) -> dict:
    """扫码登录统一入口：先确保有头、再打开登录页，一次调用完成。

    无头账号直接 open_login 没有可见窗口、无法扫码，故先 set_window(on=True)
    切回有头（重启引擎、回注 cookie）；本就有头则跳过切换。覆盖两类场景：
    未登录首次扫码、登录态过期重新扫码。
    """
    from src import config
    from src.server import _COLLECT_TASKS

    if config.get_account(account) is None:
        return {"ok": False, "error": f"账号 {account} 不在注册表中"}
    for _st in list(_COLLECT_TASKS.values()):
        if _st.get("status") == "running" and _st.get("account_id", "default") == account:
            return {"ok": False, "error": "有采集任务进行中，请先停止再扫码登录"}
    if config.account_headless(account):
        win = await set_window(account, on=True)
        if not win.get("ok"):
            return win
    await open_login(account)
    xlog.audit(account, action="scan_login", ok=True)
    return {"ok": True, "account": account, "note": "已打开扫码窗口，请扫码登录"}


async def set_window(account: str = "default", on: bool = True) -> dict:
    """调试实时窗口开关：有头小窗 ↔ 无头，引擎重启生效。

    on=True：headless=False，引擎独立小窗（960x720）实时可见采集/登录操作过程
    （替代原「截图映射」调试视图——用户明确不要截图轮询）。
    on=False：回无头（默认生产形态）。
    切换需重启引擎：先优雅关闭（落盘登录态）→ 更新配置 → 重新启动（cookie 回注保留登录态）。
    若该账号有采集任务运行中，先拒绝（避免任务中断）。
    """
    from src import config
    from src.server import _COLLECT_TASKS

    # launcher 会判定用户关闭了应用并退出整个进程（实测：API 先返回 200，随后应用自己退出，
    # 表现为「点了一下，整个程序没了」）。所以这里必须拒绝，不能静默放行。
    if account == "default" and not on:
        return {"ok": False, "error": (
            "default 账号承载 APP 壳窗口与采集窗，运行中切无头会关闭整个应用；"
            "如需后台运行请用其他账号，或以无头模式整体启动"
        )}

    if _collect_running(account, _COLLECT_TASKS):
        return {"ok": False, "error": "有采集任务进行中，请先停止再切换调试窗口"}
    want_headless = not on
    # 下次启动也变成无头（default 因此丢窗口），多账号必须各管各的。
    if config.account_headless(account) == want_headless:
        return {"ok": True, "window": on, "note": "已是目标模式，无需切换"}
    if not config.set_account_headless(account, want_headless):
        return {"ok": False, "error": f"账号 {account} 不在注册表中，无法切换窗口"}
    await engine_pool.pool.close(account)          # 优雅关闭：落盘登录态
    eng = await engine_pool.pool.get(account)       # 重新启动（读新 headless + 回注 cookie）
    await eng.context()
    xlog.audit(account, action="mirror_window", ok=True,
               detail=f"window={'on' if on else 'off'}")
    return {"ok": True, "window": on,
            "note": "引擎已重启，独立小窗已打开（实时可见操作）" if on else "已回无头模式"}


def _collect_running(account: str, tasks) -> bool:
    """该账号是否有采集任务正在跑（任务结构由 server 侧维护）。"""
    return any(
        st.get("status") == "running" and st.get("account_id", "default") == account
        for st in list(tasks.values())
    )


async def close_account(account: str = "default") -> dict:
    """关闭账号引擎，释放 Chromium 进程与独占 CDP 端口。

    第三类运营账号窗按账号数开出来，就必须能按账号收回去——之前只有
    `POST /api/mirror/open`，关窗只能去命令行杀进程，等于没有窗口管理。

    default 拒绝关闭：它承载 APP 壳窗口与采集窗，关掉 = launcher 判定用户
    退出应用（与 set_window 的同一个根因）。
    """
    from src.server import _COLLECT_TASKS

    if account == "default":
        return {"ok": False, "error": (
            "default 账号承载 APP 壳窗口与采集窗，不能单独关闭；"
            "要退出整个应用请用应用退出"
        )}

    for _st in list(_COLLECT_TASKS.values()):
        if _st.get("status") == "running" and _st.get("account_id", "default") == account:
            return {"ok": False, "error": "该账号有采集任务进行中，请先停止再关闭窗口"}

    if engine_pool.pool.peek(account) is None:
        return {"ok": True, "account": account, "note": "引擎未运行，无需关闭"}
    if not await engine_pool.pool.close(account):
        return {"ok": True, "account": account, "note": "引擎未运行，无需关闭"}

    engine_pool.pool.mark_hidden(account)
    xlog.audit(account, action="mirror_close", ok=True, detail="engine closed")
    return {"ok": True, "account": account, "note": "引擎已关闭，进程与 CDP 端口已释放"}
