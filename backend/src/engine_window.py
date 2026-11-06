"""引擎窗口控制（原名 mirror.py，R-6.53z8q 按职责改名）。

历史与现状：
- R 批 v1.4 的设计是「截图流 + 事件转发」模拟远程桌面（左窗内嵌引擎截图、鼠标事件回传）。
- R-6.15 用户改口径为「**实时引擎窗口**」——引擎开成有头小窗，人直接看真窗口操作。
  旧的截图映射与事件转发随之失去意义，已于 R-6.53z8p 删除，避免留孤儿代码。

现有能力：
- open_login(account)：确保引擎里有一个小红书页（无则开 /login）；供起引擎的脚本调用
- set_window(account, on)：调试实时窗口开关（有头小窗 ↔ 无头；引擎重启生效，登录态回注保留）

注意：登录页不再由本模块"驱动"——第二窗口（采集窗）自主打开与刷新，此处只保证页面存在。
"""
import asyncio

from src import engine_pool, logger as xlog

log = xlog.get("mirror")

XHS_EXPLORE = "https://www.xiaohongshu.com/explore"
# R-6.53z8：登录小窗只允许 /login；已登录时平台自己会跳 /explore
XHS_LOGIN = "https://www.xiaohongshu.com/login"


async def login_page(account: str = "default"):
    """取镜像目标页：引擎活动页中的小红书页；无则新建并打开 explore（触发登录弹窗）。"""
    eng = await engine_pool.pool.get(account)
    ctx = await eng.context()
    for p in ctx.pages:
        try:
            if not p.is_closed() and (p.url or "").startswith("http") and "xiaohongshu" in p.url:
                return p
        except Exception:  # noqa: BLE001
            continue
    page = await eng.new_page()
    await page.goto(XHS_LOGIN, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)  # 等 SPA 登录弹窗渲染稳定
    return page


async def open_login(account: str = "default") -> dict:
    """登录小窗打开：确保引擎打开小红书登录页（截图流/事件转发的目标页就绪）。"""
    await login_page(account)
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

    for _st in list(_COLLECT_TASKS.values()):
        if _st.get("status") == "running" and _st.get("account_id", "default") == account:
            return {"ok": False, "error": "有采集任务进行中，请先停止再切换调试窗口"}
    cfg = config.get_config()
    want_headless = not on
    if bool(cfg.get("headless", True)) == want_headless:
        return {"ok": True, "window": on, "note": "已是目标模式，无需切换"}
    config.set_config("headless", want_headless)
    await engine_pool.pool.close(account)          # 优雅关闭：落盘登录态
    eng = await engine_pool.pool.get(account)       # 重新启动（读新 headless + 回注 cookie）
    await eng.context()
    xlog.audit(account, action="mirror_window", ok=True,
               detail=f"window={'on' if on else 'off'}")
    return {"ok": True, "window": on,
            "note": "引擎已重启，独立小窗已打开（实时可见操作）" if on else "已回无头模式"}
