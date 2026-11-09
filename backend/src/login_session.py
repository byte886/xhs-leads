"""统一登录会话（事件驱动）：扫码登录成功 → 自动切无头。

替代的轮询守护（每 2.5s read_cookies 查 id_token）。真机调研确认的信号：
- 未登录 ``GET /api/sns/web/v2/user/me`` 返回 ``data.guest=true``；
- 扫码登录成功后前端重新拉取该接口，返回 ``data.guest=false`` + 真实 nickname。
监听挂在 **context 级**（页面导航/重建不影响），事件触发后再一次性 read_cookies
确认 id_token（项目权威登录口径），等页面稳定后切无头，全程无轮询。

边界：引擎整体崩溃重建会产生新 context，本会话持有的旧监听随之失效；该场景由
上层重新发起登录会话（本会话是扫码期间的短期对象，带 5 分钟超时）。
"""
import asyncio

from src import engine_pool, logger as xlog

log = xlog.get("login")

USER_ME_MARK = "/api/sns/web/v2/user/me"


def is_real_login_me(payload: dict) -> bool:
    """纯函数：判定 user/me 响应是否为真实登录（data.guest 严格为 False）。

    游客/异常响应（guest=true、data 缺失、字段缺失）一律返回 False，避免误判。
    """
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return False
    return data.get("guest") is False


def login_nickname(payload: dict) -> str:
    """纯函数：从 user/me 响应取平台昵称（非字符串/空白一律返回空串）。

    登录成功那一刻 data.nickname 就是真实昵称（实测为中文昵称），
    顺手缓存进注册表，账号列表就不用为 N 个账号各连一次 CDP。
    """
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return ""
    nickname = data.get("nickname")
    return nickname.strip() if isinstance(nickname, str) else ""


def login_identity(payload: dict) -> dict:
    """纯函数：从 user/me 响应取账号的三种标识（登录成功那一刻一次性落盘）。

    - ``nickname`` 平台昵称（用户可见，会变，界面主名称首选）
    - ``red_id``   小红书号（App「我」页面能看到的那串数字，唯一且稳定，用来核对）
    - ``uid``      平台内部 ``user_id``（24 位 hex，界面不直接展示，仅兜底）

    实测 default 响应：``{user_id: "abcdef...0001", nickname: "<昵称>", red_id: "123456789"}``。
    非字符串/空白一律返回空串——宁可为空，也不写脏值进注册表。
    """
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return {"nickname": "", "red_id": "", "uid": ""}

    def _str_field(key: str) -> str:
        v = data.get(key)
        return v.strip() if isinstance(v, str) else ""

    return {
        "nickname": _str_field("nickname"),
        "red_id": _str_field("red_id"),
        "uid": _str_field("user_id"),
    }


def cache_identity(account_id: str, payload: dict) -> dict:
    """把 user/me 里的账号标识写进注册表，返回实际解析出的三者。

    只在**非空且与现值不同**时写：平台偶发不返回某个字段时，已有的值不该被空值冲掉
    （昵称尤其——它比 red_id 更容易缺失）。
    """
    from src import config  # noqa: PLC0415（避免循环导入）

    ident = login_identity(payload)
    if ident["nickname"] and config.account_nickname(account_id) != ident["nickname"]:
        config.set_account_nickname(account_id, ident["nickname"])
    if ident["red_id"] and config.account_red_id(account_id) != ident["red_id"]:
        config.set_account_red_id(account_id, ident["red_id"])
    if ident["uid"] and config.account_platform_uid(account_id) != ident["uid"]:
        config.set_account_platform_uid(account_id, ident["uid"])
    return ident


async def capture_user_me(account_id: str, timeout: float = 20.0) -> dict | None:
    """读一次该账号的 user/me 响应（返回 payload；取不到返回 None）。

    为什么不在页面里 fetch：小红书该接口要 x-s 签名，裸 ``fetch`` 会被拦
    （实测返回非 JSON 文本 "create inv..."）。这里让**页面自己发请求**——
    挂 context 级 response 监听后 reload 一次，实测一次 reload 捕获 4 次 user/me。

    用途：历史账号在「登录事件顺带缓存昵称」上线前就登录过，列表里认不出是谁，
    需一次手动补。频繁调用会多一次页面 reload，故不做定时刷新。
    """
    from src import engine_window  # noqa: PLC0415（避免循环导入）

    eng = engine_pool.pool.peek(account_id)
    if eng is None:
        return None
    ctx = await eng.context()
    page = await engine_window.login_page(account_id)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()

    async def _inspect(resp):
        if fut.done():
            return
        try:
            payload = await resp.json()
        except Exception:  # noqa: BLE001（非 JSON/读取失败，忽略该次）
            return
        if not fut.done():
            fut.set_result(payload)

    def _on_response(resp):
        if USER_ME_MARK in resp.url:
            asyncio.create_task(_inspect(resp))

    ctx.on("response", _on_response)
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        return await asyncio.wait_for(fut, timeout=timeout)
    except Exception:  # noqa: BLE001（导航失败/超时：按取不到处理，由调用方提示）
        return None
    finally:
        ctx.remove_listener("response", _on_response)
