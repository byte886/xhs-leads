"""镜像层（R 批 v1.4）：登录小窗 / 调试视图共用的画面与输入转发。

设计口径（用户对齐，见 docs/需求变更-App内呈现形态.md §三）：
- 镜像 = 「CDP 截图流 + 事件转发」，**不是 AI 看图**；唯一必须显示画面的交互场景是登录（R3 小窗）。
- 调试视图（R4）按需开启引擎实时画面，复用本模块；登录自动开 / 调试手动开 / 平时关。
- 与审计日志（logger.py 落盘）区分：镜像不落盘、不留痕。

原子能力（供 server.py 接口层调用）：
- open_login(account)：确保引擎打开小红书 explore（触发登录弹窗），登录小窗打开时调用
- screenshot_jpeg(account)：引擎镜像页截图 → JPEG bytes（前端 <img> 轮询显示）
- send_event(account, evt)：事件转发 click / type / scroll / drag（坐标相对镜像视口 CSS 像素）

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
