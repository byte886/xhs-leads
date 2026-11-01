"""镜像层（R 批 v1.4 / R-6.15）：登录小窗 / 调试视图共用的画面与输入转发。

设计口径（用户对齐，见 docs/需求变更-App内呈现形态.md §三）：
- 镜像 = 「CDP 截图流 + 事件转发」，**不是 AI 看图**；唯一必须显示画面的交互场景是登录（R3 小窗）。
- 调试视图（R4）按需开启引擎实时画面，复用本模块；登录自动开 / 调试手动开 / 平时关。
- R-6.15 用户需求变更：调试视图从「截图映射」改为「**实时引擎窗口**」——开关切换引擎
  为有头小窗（headless=False + window-size=960,720），独立小窗实时可见采集/登录操作过程。
- 与审计日志（logger.py 落盘）区分：镜像不落盘、不留痕。

原子能力（供 server.py 接口层调用）：
- open_login(account)：确保引擎打开小红书 explore（触发登录弹窗），登录小窗打开时调用
- screenshot_jpeg(account)：引擎镜像页截图 → JPEG bytes（登录小窗轮询显示）
- send_event(account, evt)：事件转发 click / type / scroll / drag（坐标相对镜像视口 CSS 像素）
- set_window(account, on)：调试实时窗口开关（有头小窗 ↔ 无头；引擎重启生效，登录态回注保留）

页面目标：登录=小红书页（explore/登录页）；调试=当前活动小红书页。页面常驻引擎，
登录态在引擎独立 profile 内（R2），镜像只读画面、转发输入。
"""
import asyncio

from src import engine_pool, logger as xlog

log = xlog.get("mirror")

XHS_EXPLORE = "https://www.xiaohongshu.com/explore"


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
    await page.goto(XHS_EXPLORE, wait_until="domcontentloaded", timeout=30000)
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


async def screenshot_jpeg(account: str = "default") -> bytes:
    """引擎镜像页截图 → JPEG bytes（登录小窗/调试视图画面；不落盘）。"""
    page = await login_page(account)
    shot = await page.screenshot(type="jpeg", quality=70)
    return shot


async def send_event(account: str, evt: dict) -> dict:
    """转发用户交互到引擎镜像页（坐标相对视口 CSS 像素；type 前需先 click 聚焦）。

    支持：click {x,y} / type {text} / scroll {dx,dy} / drag {x1,y1,x2,y2}
    """
    page = await login_page(account)
    kind = (evt.get("type") or "").lower()
    if kind == "click":
        await page.mouse.click(int(evt["x"]), int(evt["y"]))
    elif kind == "type":
        # 拟人节奏：30ms/字符（风控 friendly；前端先 click 聚焦输入框）
        await page.keyboard.type(str(evt.get("text") or ""), delay=30)
    elif kind == "scroll":
        await page.mouse.wheel(int(evt.get("dx") or 0), int(evt.get("dy") or 0))
    elif kind == "drag":
        x1, y1 = int(evt.get("x1", 0)), int(evt.get("y1", 0))
        x2, y2 = int(evt.get("x2", 0)), int(evt.get("y2", 0))
        await page.mouse.move(x1, y1)
        await page.mouse.down()
        for i in range(1, 6):  # 分段移动，模拟人手轨迹
            await page.mouse.move(x1 + (x2 - x1) * i // 5, y1 + (y2 - y1) * i // 5)
        await page.mouse.up()
    else:
        return {"ok": False, "error": f"不支持的事件类型: {kind}"}
    xlog.audit(account, action="mirror_event", ok=True, detail=f"type={kind}")
    return {"ok": True}
