"""引擎窗口控制（原名 mirror.py，R-6.53z8q 按职责改名）。

历史与现状：
- R 批 v1.4 的设计是「截图流 + 事件转发」模拟远程桌面（左窗内嵌引擎截图、鼠标事件回传）。
- R-6.15 用户改口径为「**实时引擎窗口**」——引擎开成有头小窗，人直接看真窗口操作。
  旧的截图映射与事件转发随之失去意义，已于 R-6.53z8p 删除，避免留孤儿代码。

现有能力：
- open_login(account)：确保引擎里有一个小红书页（无则开 explore，未登录自动弹登录 modal）；供起引擎的脚本调用
- set_window(account, on)：调试实时窗口开关（有头小窗 ↔ 无头；引擎重启生效，登录态回注保留）

注意：登录页不再由本模块"驱动"——第二窗口（采集窗）自主打开与刷新，此处只保证页面存在。
"""
import asyncio

from src import engine_pool, logger as xlog

log = xlog.get("mirror")

XHS_EXPLORE = "https://www.xiaohongshu.com/explore"
# 历史上登录小窗只允许 /login（full-page 独立页）；R-6.66 起统一走 explore
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


async def set_window(account: str = "default", on: bool = True) -> dict:
    """调试实时窗口开关（R-6.15）：有头小窗 ↔ 无头，引擎重启生效。

    on=True：headless=False，引擎独立小窗（960x720）实时可见采集/登录操作过程
    （替代原「截图映射」调试视图——用户明确不要截图轮询）。
    on=False：回无头（默认生产形态）。
    切换需重启引擎：先优雅关闭（落盘登录态）→ 更新配置 → 重新启动（cookie 回注保留登录态）。
    若该账号有采集任务运行中，先拒绝（避免任务中断）。
    """
    from src import config
    from src.server import _COLLECT_TASKS

    # R-6.58：default 账号的引擎同时承载 APP 壳窗口与采集窗，关掉引擎 = 两个窗口一起消失，
    # launcher 会判定用户关闭了应用并退出整个进程（实测：API 先返回 200，随后应用自己退出，
    # 表现为「点了一下，整个程序没了」）。所以这里必须拒绝，不能静默放行。
    if account == "default" and not on:
        return {"ok": False, "error": (
            "default 账号承载 APP 壳窗口与采集窗，运行中切无头会关闭整个应用；"
            "如需后台运行请用其他账号，或以无头模式整体启动"
        )}

    for _st in list(_COLLECT_TASKS.values()):
        if _st.get("status") == "running" and _st.get("account_id", "default") == account:
            return {"ok": False, "error": "有采集任务进行中，请先停止再切换调试窗口"}
    want_headless = not on
    # R-6.58：按账号记 headless，不写全局——否则切 A 账号的无头会把 B 账号
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


async def close_account(account: str = "default") -> dict:
    """关闭账号引擎，释放 Chromium 进程与独占 CDP 端口（R-6.60）。

    第三类运营账号窗按账号数开出来，就必须能按账号收回去——之前只有
    `POST /api/mirror/open`，关窗只能去命令行杀进程，等于没有窗口管理。

    default 拒绝关闭：它承载 APP 壳窗口与采集窗，关掉 = launcher 判定用户
    退出应用（与 set_window 的 R-6.58 同一个根因）。
    """
    from src import config
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


async def minimize_account(account: str = "default") -> dict:
    """瞬时收起窗口（最小化，不重启、不打断任务、page 不失效）。

    与 set_window(on=False) 切无头的区别：切无头要重启引擎（20-40s、page 失效），
    最小化是同一有头引擎内 0.5 秒级的窗口状态，CDP 与页面全程保持。
    """
    from src import config

    if account == "default":
        return {"ok": False, "error": "default 承载 APP 壳窗与采集窗，不做最小化收起"}
    eng = engine_pool.pool.peek(account)
    if eng is None:
        return {"ok": True, "account": account, "note": "引擎未运行，无需收起"}
    if config.account_headless(account):
        return {"ok": True, "account": account, "note": "已是无头，没有窗口"}
    if not await eng.minimize_window():
        return {"ok": False, "account": account, "error": "最小化未确认（窗口状态未变为 minimized）"}
    engine_pool.pool.mark_hidden(account)
    xlog.audit(account, action="mirror_minimize", ok=True)
    return {"ok": True, "account": account, "note": "窗口已最小化，任务与 CDP 保持运行"}


async def restore_account(account: str = "default") -> dict:
    """把最小化的有头窗口恢复到账号级联位置。"""
    from src import config

    if account == "default":
        return {"ok": False, "error": "default 窗口不通过此接口操作"}
    eng = engine_pool.pool.peek(account)
    if eng is None:
        return {"ok": False, "account": account, "error": "引擎未运行，先打开账号窗口"}
    if config.account_headless(account):
        return {"ok": False, "account": account,
                "error": "当前是无头模式；要看窗口请用 window 切换有头"}
    # 恢复前先保证可见窗口不超上限：超了把最旧的最小化，给本账号腾位置
    made_room = await engine_pool.pool.enforce_visible_cap(account)
    if not await eng.restore_window():
        return {"ok": False, "account": account, "error": "恢复未确认"}
    engine_pool.pool.mark_visible(account)
    xlog.audit(account, action="mirror_restore", ok=True)
    return {"ok": True, "account": account,
            "note": "窗口已恢复到级联位置",
            "made_room": made_room}


async def minimize_all_accounts() -> dict:
    """全部收起：把所有在跑的有头第三类账号最小化（瞬时、不重启、不打断任务）。

    只动「在跑且有头」的账号；无头账号本来就没窗口、引擎没开的跳过；default 不动。
    """
    from src import config

    changed = []
    for aid in config.list_accounts():
        if aid == "default":
            continue
        eng = engine_pool.pool.peek(aid)
        if eng is None or config.account_headless(aid):
            continue
        if await eng.minimize_window():
            changed.append(aid)
            engine_pool.pool.mark_hidden(aid)
    xlog.audit("all", action="mirror_minimize_all", ok=True, detail=f"{len(changed)} 个")
    return {"ok": True, "minimized": changed,
            "note": f"已收起 {len(changed)} 个窗口，default 不受影响"}


# ---------------- R-6.67 登录完成自动切无头（纯事件驱动）

async def start_auto_headless(account: str) -> dict:
    """开启「登录后自动切无头」事件监听（开窗扫码时调用）。

    实现与事件循环在 login_session.LoginSession：监听 context 级 user/me 响应
    （guest=false 即登录成功），无轮询。这里只做校验与兼容返回结构。
    已是无头 / 已在监听给出幂等提示；default 与不存在的账号拒绝。
    """
    from src import config, login_session  # noqa: PLC0415

    if account == "default":
        return {"ok": False, "error": "default 承载 APP 壳窗与采集窗，不做自动切无头"}
    if config.get_account(account) is None:
        return {"ok": False, "error": f"账号 {account} 不存在"}
    if config.account_headless(account):
        return {"ok": True, "account": account, "note": "当前已是无头，无需再等登录"}
    if login_session.start_login_session(account) is None:
        return {"ok": True, "account": account, "note": "已在等待登录，无需重复开启"}
    return {"ok": True, "account": account,
            "note": f"已开启事件监听，登录成功后 {login_session.LOGIN_STABLE}s 自动切无头"}


async def cancel_auto_headless(account: str) -> dict:
    """取消「登录后自动切无头」事件监听。"""
    from src import config, login_session  # noqa: PLC0415

    config.set_account_auto_headless(account, False)
    await login_session.stop_login_session(account)
    return {"ok": True, "account": account, "note": "已取消自动切无头监听"}
