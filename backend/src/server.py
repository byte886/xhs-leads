"""G2 后端壳：把 xiaohongshu-mcp 封装成 REST API + SQLite 读写 + 静态前端服务。

运行:
  uvicorn server:app --host 127.0.0.1 --port 8791

API:
  GET  /                    → 前端界面（app/web/index.html）
  GET  /api/login_status    → 登录状态
  POST /api/search          → 搜索+详情补全+评分+落库（同步返回新增数）
  GET  /api/notes           → 笔记列表（评分排序，支持关键词过滤）
  GET  /api/note/{feed_id}  → 单条详情（含 desc/图片列表）
  POST /api/clone           → 一键克隆：拉详情+下载图片，返回可编辑载荷
  POST /api/publish         → 发布（默认仅自己可见）
  GET  /api/logs            → 发布记录
"""
import datetime
import io
import json
import os
import subprocess
import sys
import random
import sqlite3
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---- 统一日志（审计账本） ----
from src import logger as _log

log = _log.get("server")

# ---- 路径 ----
# 安装根与数据根分离（壳 B 批）：打包态下数据目录由 launcher 注入 XHS_LEADS_DATA
# （~/Library/Application Support/xhs-leads/data），静态资源仍取自安装根。
if getattr(sys, "_MEIPASS", None):  # PyInstaller 打包态：_MEIPASS 为 bundle 资源根
    BASE = Path(sys._MEIPASS)
else:
    BASE = Path(__file__).resolve().parents[2]  # 项目根（server.py 在 backend/src/ 下）
_APP_DATA = os.environ.get("XHS_LEADS_DATA")
DATA_DIR = Path(_APP_DATA).expanduser() if _APP_DATA else BASE / "data"
DB_PATH = DATA_DIR / "xhs_notes.db"
WEB_DIR = BASE / "frontend" / "dist"
IMAGES_DIR = DATA_DIR / "images"

_INTERVAL = (1.0, 1.8)

from src.score import rank_notes, _to_int  # noqa: E402
from src.jsonio import read_json_safe, write_json  # noqa: E402
from src.collect_pipeline import (  # noqa: E402
    get_feed_detail,
    get_note_detail_by_url,
    get_user_profile,
    search_via_browser,
)
from src import browser_client  # noqa: E402（快速档 _enrich_notes 直连详情/主页）
from src import config, risk  # noqa: E402（架构改造 P1：配置中心 + 每账号风控）
from src import license as lic  # noqa: E402（授权薄层：free 恒放行，业务入口统一过闸）

app = FastAPI(title="XHS Leads · 小红书获客助手")

# ---- 拟人频率保护（平台风控：操作过频触发临时行为控制，需限流）----
# 架构改造 P1：全局节流 → risk 每账号节流（见 _check_collect_throttle / _mark_collect_done）


def _check_collect_throttle(account_id: str = "default") -> None:
    """采集/搜索前的频率闸（每账号，替代旧全局节流）：超闸抛 400。"""
    try:
        risk.check_throttle(account_id or "default")
    except risk.BlockedError as e:
        raise HTTPException(400, e.reason) from e


def _mark_collect_done(account_id: str = "default") -> None:
    """平台任务结束（成功/停止/失败）统一记录结束时间，供节流闸使用。"""
    risk.mark_collect_done(account_id or "default")


@app.on_event("startup")
async def _startup_cleanup():
    """启动时清理：残留任务 tab + 无引用本地图片。best-effort 不阻塞启动。"""
    try:
        from src import browser_client
        await asyncio.wait_for(browser_client.cleanup_tabs(), timeout=15)
    except Exception:  # noqa: BLE001
        pass
    try:
        _cleanup_orphan_images()
    except Exception:  # noqa: BLE001
        pass


@app.on_event("shutdown")
async def _shutdown_close_engines():
    """后端退出钩子：优雅关闭全部账号引擎（ctx.close() 落盘登录态，绝不用 pkill -9）。"""
    try:
        from src import browser_client
        await asyncio.wait_for(browser_client.stop(), timeout=20)
    except Exception as e:  # noqa: BLE001
        print(f"[shutdown] 引擎关闭异常（非阻断）: {e}")


@app.post("/api/cleanup_images")
def cleanup_images():
    """前端启动时触发：清理无引用本地图片（与后端 startup 双保险）。"""
    removed = _cleanup_orphan_images()
    return {"ok": True, "removed": removed}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


def _img_name(feed_id: str, suffix: str = "cover") -> str:
    """本地图片文件名：feed_id 含 #（XHS 原特征），# 在 URL 里是 fragment 会被截断 → 替换为 _。"""
    return f"{feed_id.replace('#', '_')}_{suffix}.jpg"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    _init_tables(con)
    return con


def _init_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            feed_id TEXT PRIMARY KEY, keyword TEXT, title TEXT, note_type TEXT,
            user_id TEXT, nickname TEXT, avatar TEXT, cover_url TEXT, xsec_token TEXT,
            desc TEXT, ip_location TEXT,
            liked_count INTEGER, collected_count INTEGER, comment_count INTEGER,
            shared_count INTEGER, fans_count INTEGER, publish_time INTEGER,
            score REAL, tag TEXT, fan_ratio REAL, fetched_at TEXT, score_info TEXT,
            video_url TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS publish_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id TEXT, title TEXT, content TEXT, tags TEXT, visibility TEXT,
            status TEXT, note_url TEXT, created_at TEXT
        )
    """)
    # v3 拟人化护栏需要 raw 列（风控熔断关键词检测）；老库补列
    try:
        con.execute("ALTER TABLE publish_logs ADD COLUMN raw TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # P1 数据回流：发布记录回写互动数据（浏览/赞/藏/评/转 + 抓取时间）
    try:
        con.execute("ALTER TABLE publish_logs ADD COLUMN stats_json TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # P4 采集客户：互动用户名单（评论用户）
    con.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            uid TEXT, feed_id TEXT, nickname TEXT, comment TEXT, profile TEXT, created_at TEXT,
            intent TEXT, matched_word TEXT, ip_location TEXT, comment_time TEXT,
            PRIMARY KEY (uid, feed_id)
        )
    """)
    # O1-3：老库补意向/属地/时间列（幂等）
    for col in ("intent", "matched_word", "ip_location", "comment_time"):
        try:
            con.execute(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在
    # 评分明细（score_info JSON：4 维度得分，卡片右上角分数可解释）；老库补列
    try:
        con.execute("ALTER TABLE notes ADD COLUMN score_info TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # 视频克隆：详情页原视频地址（extract.js 提取），供一键克隆下载原视频
    try:
        con.execute("ALTER TABLE notes ADD COLUMN video_url TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # 存草稿（长期保留资产；区别于搜索/采集结果——用完即失效）
    con.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id TEXT, title TEXT, content TEXT, tags TEXT,
            visibility TEXT, status TEXT DEFAULT 'draft', created_at TEXT
        )
    """)
    # 草稿图片持久化（本地路径 JSON）；老库补列
    try:
        con.execute("ALTER TABLE drafts ADD COLUMN images TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # 架构改造 P1：多账户维度 —— 各表补 account_id（默认 default，老数据兼容）
    for _tbl in ("notes", "publish_logs", "clients", "drafts"):
        try:
            con.execute(f"ALTER TABLE {_tbl} ADD COLUMN account_id TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # 列已存在
    con.commit()


# ================= 登录 =================
# 登录态缓存：Chrome CDP 连接会弹「远程调试授权」窗（每次连接必弹，官方无法关闭）。
# 前端轮询 login_status 若每次都连 Chrome → 每几秒弹一次窗（用户痛点「关了又开」）。
# 因此缓存检测结果 TTL=30s，轮询打缓存，不反复连 Chrome。
_LOGIN_CACHE = {"t": 0.0, "data": None}
_LOGIN_CACHE_TTL = 30.0

def _read_cookie_status(p: Path) -> tuple[bool, str, bool]:
    """【R 批 v1.5 已停用留档】读本地 cookies.json 判定登录态（方案 A「读系统 Chrome 落盘副本」遗留）。

    R 批口径：登录态只存引擎独立 profile（引擎 CDP 内存判定，见 login_status / _require_real_login）；
    本函数不再被 login_status 调用，保留供回溯与旧链路测试参考。
    返回 (logged_in, web_session 摘要, expires 是否有效)。
    expires=None 是会话级 Cookie（浏览器会话内一直有效，Chrome 导出不携带过期时间）
    → 视为有效；有 expires 且在未来才有效。
    """
    if not p.exists():
        return False, "", True
    ws, exp_ok = _parse_web_session(p)
    return bool(ws) and exp_ok, ws, exp_ok


def _parse_web_session(p: Path) -> tuple[str, bool]:
    """从 cookies.json 提取 web_session 值及其过期有效性。"""
    data = json.loads(p.read_text(encoding="utf8"))
    cookies = data.get("cookies", [])
    return _web_session_value(cookies), _expires_ok(_web_session_expires(cookies))


def _web_session_value(cookies: list) -> str:
    return next((c.get("value", "") for c in cookies if c.get("name") == "web_session"), "")


def _web_session_expires(cookies: list) -> list:
    return [c.get("expires") for c in cookies if c.get("name") == "web_session"]


def _expires_ok(exps: list) -> bool:
    """expires=None 视为会话级 Cookie（一直有效）；有 expires 且在未来才有效。"""
    if not exps:
        return True
    now_ts = int(time.time())
    return any(e is None or (e or 0) >= now_ts for e in exps)


async def _engine_login_state(eng) -> tuple[bool, bool]:
    """引擎真实登录态（R 批 v1.5）：返回 (web_session 存在?, 页面登录?)。

    只读、不清 cookie、不新建浏览器实例（复用引擎现有小红书页，无则开 explore）：
    ① CDP 读引擎内存 cookie → web_session 是否存在；
    ② 页面注入 extract.js → xhsIsLoggedIn() 双重确认。

    背景：web_session 存在 ≠ 会话有效（小红书可种入已失效 cookie，页面仍弹登录框，实测见 R-6）。
    """
    from src import browser_client

    mem = await eng.read_cookies()
    has_ws = any(c.get("name") == "web_session" for c in mem)
    page_logged_in = False
    if has_ws:
        ctx = await eng.context()
        page = None
        for p in ctx.pages:
            if not p.is_closed() and "xiaohongshu" in (p.url or ""):
                page = p
                break
        if page is None:
            page = await eng.new_page()
            await page.goto(
                "https://www.xiaohongshu.com/explore",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(3)
        await page.evaluate(browser_client._EXTRACT_SRC)
        page_logged_in = bool(await page.evaluate("() => !!xhsIsLoggedIn()"))
    return has_ws, page_logged_in


@app.get("/api/login_status")
async def login_status():
    """登录态检测（R 批 v1.5：读引擎独立 profile 真实登录态）。

    改造背景：旧实现只读本地 cookies.json（方案 A「读系统 Chrome 落盘副本」产物）——
    R 批拍板「登录态只存引擎独立 profile、不依赖系统 Chrome」后，该副本与引擎真实会话
    脱节：副本未过期而引擎早已失效（或反之），造成假阳性/假阴性。本接口改为
    **引擎 CDP 内存 Cookie 判定**（web_session 存在即视为有效，与 _require_real_login 同源）。

    - 引擎 headless 无窗口（R2），拉取无 Dock/窗口干扰；30s 缓存兜底登录轮询频率。
    - 旧 cookies.json 链路已停用留档（函数体保留可回溯，见 _read_cookie_status）。
    """
    import time

    now = time.monotonic()
    if _LOGIN_CACHE["data"] is not None and now - _LOGIN_CACHE["t"] < _LOGIN_CACHE_TTL:
        return _LOGIN_CACHE["data"]

    try:
        from src import engine_pool

        eng = await engine_pool.pool.get("default")
        has_ws, page_logged_in = await _engine_login_state(eng)
        logged_in = has_ws and page_logged_in
        result = {
            "status": "logged_in" if logged_in else "not_logged_in",
            "logged_in": logged_in,
            "nickname": "",
            "raw": f"engine-cdp web_session={'有' if has_ws else '无'} 页面登录={'是' if page_logged_in else '否'}",
        }
        # 登录恢复闭环：引擎真实登录态有效 → 自动解除该账号软风控
        if logged_in:
            try:
                risk.resolve("default", reason="登录态有效（login_status 引擎 CDP+页面）")
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        result = {"status": "unknown", "logged_in": False, "nickname": "", "raw": f"引擎判定失败: {str(e)[:80]}"}

    _LOGIN_CACHE["t"] = now
    _LOGIN_CACHE["data"] = result
    return result


# ================= CDP 自动化配置 =================
# 交付模式：APP 自动/半自动配置外部 Chrome 的远程调试（普通客户无需懂 CDP）。
# 方案（谷歌/官方调研）：Chrome 启动参数 --remote-debugging-port + 独立 profile（--user-data-dir）。
#   已运行实例不生效新参数 → Chrome 已运行但未开调试时需重启（半自动引导/确认后自动重启）。
async def _probe_cdp(port: int) -> bool:
    """探测调试端口是否可连（/json/version 通 = 已开启远程调试）。"""
    try:
        reqh = urllib.request.Request(f"http://127.0.0.1:{port}/json/version", method="GET")
        with urllib.request.urlopen(reqh, timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


class CdpSetupReq(BaseModel):
    restart: bool = False  # Chrome 已运行未开调试时，是否自动退出重启
    chrome_path: str = ""  # 用户自定义 Chrome 路径（持久化）
    profile_dir: str = ""  # 用户自定义 Profile 路径（持久化）


def _script_path(name: str) -> Path | None:
    """定位 scripts/legacy 下的生产辅助脚本，脚本不存在时返回 None。"""
    p = Path(__file__).resolve().parents[2] / "scripts" / "legacy" / name
    return p if p.exists() else None


def _platform_setup_script() -> Path | None:
    """生产环境脚本：单脚本跨平台（内部按系统分流），带参数启动 Chrome 并打开配置页。"""
    return _script_path("setup_chrome.py")


def _pick_chrome_script() -> Path | None:
    """生产环境脚本：系统文件对话框选择 Chrome（未找到可用 Chrome 时使用）。"""
    return _script_path("pick_chrome.py")


def _pick_chrome_file() -> str | None:
    """弹系统文件对话框选择 Chrome（调生产脚本 pick_chrome.py：macOS 选 .app、Windows 选 chrome.exe）。

    用户取消返回 None（不视为错误）。
    """
    script = Path(__file__).resolve().parents[2] / "scripts" / "legacy" / "pick_chrome.py"
    try:
        if not script.exists():
            return None
        out = subprocess.run([sys.executable, str(script)],
                             capture_output=True, text=True, timeout=320)
        p = out.stdout.strip()
        return p if p and os.path.exists(p) else None
    except Exception:  # noqa: BLE001
        return None


@app.post("/api/cdp_pick_chrome")
async def cdp_pick_chrome():
    """未找到 Chrome 时：弹系统文件对话框让用户指定 Chrome 可执行文件，并自动保存路径配置。"""
    from src import browser_client
    path = _pick_chrome_file()
    if not path:
        return {"ok": False, "note": "未选择文件或已取消"}
    browser_client.save_cdp_config(chrome_path=path)
    return {"ok": True, "chrome_path": path, "note": "Chrome 路径已设置，请点「刷新状态」继续"}


@app.get("/api/cdp_status")
async def cdp_status():
    """CDP 自动化状态：Chrome 是否安装/运行、调试端口是否就绪、路径（默认/用户自定义）。"""
    from src import browser_client
    port, profile_dir = browser_client._profile_settings()
    chrome = browser_client.chrome_executable()
    # 就绪判定：优先用户 profile 的 DevToolsActivePort（Chrome 144+ 无参数 inspect 开关方式），其次固定端口
    port_ready = await _probe_cdp(port)
    dev_port = None
    if not port_ready:
        dev_port = _read_devtools_active_port(profile_dir)
        if dev_port:
            port_ready = await _probe_cdp(dev_port)
    running = browser_client.chrome_running()
    # 综合状态：ready=就绪 / need_chrome=未找到Chrome / not_running=未运行(可一键启动) / need_auth=运行中但调试未就绪(未开开关/授权拒绝/需重启)
    if port_ready:
        state = "ready"
    elif chrome is None:
        state = "need_chrome"
    elif not running:
        state = "not_running"
    else:
        state = "need_auth"
    return {
        "ok": True,
        "state": state,
        "port": dev_port or port,
        "port_ready": port_ready,
        "chrome_running": running,
        "chrome_found": chrome is not None,
        "chrome_path": chrome or "",
        "profile_dir": profile_dir,
    }


def _read_devtools_active_port(profile_dir: str) -> int | None:
    """读取 Chrome 写的 DevToolsActivePort（Chrome 144+ 在 chrome://inspect 开启远程调试后生成）。

    优先读独立 profile（APP 自拉），兜底读用户日常 profile（引导方式：用户在自带 Chrome 开启开关）。
    跨平台：macOS ~/Library/Application Support/Google/Chrome；Windows %LOCALAPPDATA%\\Google\\Chrome\\User Data。
    """
    candidates = [profile_dir]
    try:
        if sys.platform == "darwin":
            candidates.append(str(Path.home() / "Library/Application Support/Google/Chrome"))
        elif os.name == "nt":
            candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"))
    except Exception:  # noqa: BLE001
        pass
    for d in candidates:
        try:
            p = Path(d) / "DevToolsActivePort"
            if p.exists():
                txt = p.read_text(encoding="utf-8").strip()
                first = txt.splitlines()[0].strip()
                if first.isdigit():
                    return int(first)
        except Exception:  # noqa: BLE001
            continue
    return None


@app.post("/api/cdp_setup")
async def cdp_setup(req: CdpSetupReq):
    """一键配置 CDP（自动/半自动，支持用户自定义 Chrome 路径 / Profile 路径）：
    - 保存用户自定义路径（持久化）；
    - 端口已就绪 → 无需动作；
    - Chrome 未运行 → 自动拉起（带调试参数 + 独立 profile，备选方案）；
    - Chrome 已运行但未开调试 → 返回 need_restart 提示；restart=true 时自动退出并重启。
    """
    from src import browser_client
    port, profile_dir = _persist_and_read_cdp(req, browser_client)
    already = await _check_cdp_ready(port, profile_dir)
    if already:
        return already
    blocked = await _check_chrome_state(req, browser_client)
    if blocked:
        return blocked
    return await _launch_cdp_chrome(port, profile_dir, browser_client)


def _persist_and_read_cdp(req, browser_client):
    """保存自定义 Chrome/Profile 路径并读取当前调试端口配置。"""
    if req.chrome_path or req.profile_dir:
        browser_client.save_cdp_config(chrome_path=req.chrome_path, profile_dir=req.profile_dir)
    return browser_client._profile_settings()


async def _check_cdp_ready(port: int, profile_dir: str):
    """探测已就绪的调试端口（配置端口 或 chrome://inspect 的 DevTools 端口）。"""
    if await _probe_cdp(port):
        return {"ok": True, "status": "ready", "note": f"CDP 已就绪（端口 {port}）"}
    dev_port = _read_devtools_active_port(profile_dir)
    if dev_port and await _probe_cdp(dev_port):
        return {"ok": True, "status": "ready", "note": f"CDP 已就绪（chrome://inspect 远程调试，端口 {dev_port}）"}
    return None


async def _check_chrome_state(req, browser_client):
    """Chrome 缺失/未开调试时的阻断分支：返回 need_restart 或重启 Chrome。"""
    if browser_client.chrome_executable() is None:
        return {"ok": False, "note": "未找到 Chrome，请填写 Chrome 路径或先安装 Google Chrome"}
    if not browser_client.chrome_running():
        return None
    if not req.restart:
        return {
            "ok": False, "need_restart": True,
            "note": "Chrome 已在运行但未开启远程调试，请退出 Chrome 后再次点击「一键配置」，或先点「打开远程调试设置」开启开关",
        }
    browser_client.quit_chrome()
    await asyncio.sleep(2)  # 等 Chrome 完全退出再重新拉起
    return None


async def _launch_cdp_chrome(port: int, profile_dir: str, browser_client):
    """带调试参数启动 Chrome（平台脚本或直启）并轮询等待调试端口就绪。

    B 方案（引擎模式，macOS 走 else 分支）：引擎由 engine_pool 自动管理，
    打开小红书登录页即完成「配置」，无需远程调试端口。
    """
    script = _platform_setup_script()
    if script is not None:
        r = subprocess.run([sys.executable, str(script), str(port), profile_dir],
                           capture_output=True, text=True, timeout=20)
        out = (r.stdout or "").strip()
        if out == "CHROME_NOT_FOUND":
            return {"ok": False, "note": "未找到 Chrome，请检查 Chrome 路径设置"}
        if out == "CHROME_RUNNING":
            return {"ok": False, "need_restart": True,
                    "note": "Chrome 正在运行，请退出后重试，或点「打开远程调试设置」开启开关"}
        for _ in range(30):
            await asyncio.sleep(1)
            if await _probe_cdp(port):
                return {"ok": True, "status": "ready", "note": "CDP 已配置完成（Chrome 已带调试参数启动，独立 profile）"}
        return {"ok": False,
                "note": "Chrome 已启动但未检测到远程调试开启——可能您未在 Chrome 弹窗中点「允许」。本次配置已停止：请重新点击「一键配置 CDP」，在 Chrome 弹窗中选择「允许」后再继续；若选择「不允许」，远程调试功能将无法使用。"}
    # B 方案（引擎模式）：无需远程调试端口，打开登录页即完成
    await browser_client.launch_profile_chrome()
    return {"ok": True, "status": "ready",
            "note": "已打开小红书登录页（引擎自动管理，无需远程调试配置）。请在 app 内登录小窗完成登录（扫码/手机号/微信任一途径）。"}


@app.post("/api/cdp_pick_chrome")
async def cdp_pick_chrome():
    """未找到可用 Chrome 时：弹出系统文件对话框让用户指定 Chrome 路径（保存后自动生效）。"""
    from src import browser_client
    script = _pick_chrome_script()
    if script is None:
        return {"ok": False, "note": "当前系统不支持自动选择文件，请在下方手动填写 Chrome 路径"}
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=320)
    picked = (r.stdout or "").strip()
    if not picked or not os.path.exists(picked):
        return {"ok": False, "note": "未选择 Chrome 或路径无效，可手动在输入框填写后保存"}
    browser_client.save_cdp_config(chrome_path=picked)
    return {"ok": True, "chrome_path": picked, "note": f"已选择 Chrome：{picked}"}


@app.post("/api/cdp_open_inspect")
async def cdp_open_inspect():
    """引导方式：打开 chrome://inspect/#remote-debugging（Chrome 144+ 官方免启动参数开启远程调试）。

    由用户在此页面开启「Allow remote debugging」，然后返回本页点「刷新状态」。
    """
    from src import browser_client
    if browser_client.chrome_executable() is None:
        return {"ok": False, "note": "未找到 Chrome，请填写 Chrome 路径或先安装 Google Chrome"}
    await browser_client.open_inspect_remote_debugging()
    return {"ok": True, "note": "已打开小红书登录页（引擎自动管理调试能力，无需远程调试设置）。请在 app 内登录小窗完成登录后返回本页继续"}


@app.get("/api/qrcode")
async def qrcode():
    """Cookie 失效时前端引导登录（R 批 v1.4）：登录在 app 内登录小窗完成（引擎独立 profile，
    镜像=截图流+事件转发，途径不限）。本接口确保引擎打开小红书登录页并返回引导信息。
    前端配合轮询 /api/login_status 直到登录成功。"""
    try:
        from src import mirror
        await mirror.open_login("default")
        return {
            "ok": True,
            "guide": "app 内登录小窗已打开：请扫码、或手机号+验证码、或微信登录（任一途径），登录成功后自动继续。",
            "url": "https://www.xiaohongshu.com/explore",
            "expires_in": 300,
        }
    except Exception as e:
        return {"ok": False, "error": f"打开小红书登录页失败：{str(e)[:100]}"}


# ================= 镜像层（R 批 v1.4：登录小窗 / 调试视图共用） =================
# 后端 CDP 桥：截图流（captureScreenshot）+ 事件转发（click/type/drag/scroll）。
# 口径（用户对齐）：镜像=「画面+输入转发」不是 AI 看图；登录自动开 / 调试手动开 / 平时关；
# 不落盘（区别于审计日志）。前端实现见 R3/R4。

class MirrorEventReq(BaseModel):
    """事件转发请求：type 支持 click{ x,y } / type{ text } / scroll{ dx,dy } / drag{ x1,y1,x2,y2 }。"""
    account: str = "default"
    type: str
    x: int = 0
    y: int = 0
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    dx: int = 0
    dy: int = 0
    text: str = ""


@app.post("/api/mirror/open")
async def mirror_open(account_id: str = "default"):
    """登录小窗打开：确保引擎打开小红书登录页（截图流/事件转发的目标页就绪）。"""
    from src import mirror
    return await mirror.open_login(account_id)


@app.get("/api/mirror/screenshot")
async def mirror_screenshot(account_id: str = "default"):
    """登录小窗 / 调试视图画面：引擎镜像页 JPEG（轮询显示；不落盘）。"""
    from src import mirror
    data = await mirror.screenshot_jpeg(account_id)
    return Response(
        content=data, media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/mirror/event")
async def mirror_event(req: MirrorEventReq):
    """事件转发：click/type/scroll/drag（坐标相对镜像视口 CSS 像素；type 前需先 click 聚焦）。"""
    from src import mirror
    return await mirror.send_event(req.account, req.model_dump())


class SearchReq(BaseModel):
    keyword: str
    limit: int = 20
    # 搜索筛选（对应小红书「筛选」弹层，中文选项；默认值=不筛选）
    sort: str = ""
    note_type: str = ""
    time_range: str = ""
    scope: str = ""
    distance: str = ""
    # 快速档：同页连续导航补全（去拟人等待），默认关（拟人串行，安全优先）
    fast: bool = False
    # 多账户（P1）：使用哪个账号引擎采集；空 = default（向后兼容）
    account_id: str = ""


# ====== R 批 v1.4 下线留档（方案 A/B 旧链路，不删除、保留可回溯） ======
# 以下函数均属「读系统/目标 Chrome Cookie 库自动续命」旧链路（方案 A 遗留）。
# R 批拍板：登录态 100% 只依赖引擎独立 profile（R2），不再读系统 Chrome。
# 函数保留仅供回溯与测试参照，正式链路不调用（见 _require_real_login R 批版）。


def _target_chrome_cookie_db(profile_dir: str) -> str | None:
    """目标调试 Chrome（独立 Profile）的 Cookie 库路径。

    Chrome 156+ 在 Default/Network/Cookies；旧版本在 Default/Cookies。
    返回 None 表示独立 Profile 尚未创建 / 未初始化（CDP 未配置或从未登录）。
    """
    for cand in (
        os.path.join(profile_dir, "Default", "Network", "Cookies"),
        os.path.join(profile_dir, "Default", "Cookies"),
    ):
        if os.path.exists(cand):
            return cand
    return None


def _chrome_cookies_to_saved(cookies: list) -> list:
    """cookie_reader 读取结果（{name,value,domain,path,expires}）→ cookies.json 落盘格式。

    cookies.json 供 Playwright add_cookies 注入，需 secure/httpOnly/sameSite：
    按域名推断 secure（点前缀 https-only 域名），httpOnly/sameSite 取常见安全默认。
    """
    out = []
    for c in cookies:
        if not c.get("name") or not c.get("value"):
            continue
        domain = c.get("domain", "")
        out.append({
            "name": c["name"],
            "value": c["value"],
            "domain": domain,
            "path": c.get("path") or "/",
            "secure": domain.startswith("."),
            "httpOnly": c.get("name") in ("web_session", "a1", "xsecappid", "gid"),
            "sameSite": "Lax",
        })
    return out


def _persist_restored_cookies(cookies: list) -> int:
    """把恢复的 Cookie 落盘 cookies.json（方案 A 格式），返回条数。"""
    import datetime as _dt
    p = DATA_DIR / "cookies.json"
    payload = {
        "cookies": _chrome_cookies_to_saved(cookies),
        "imported_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf8")
    return len(payload["cookies"])


def _has_valid_web_session(cookies: list[dict], now_us: int) -> bool:
    """cookie 列表中是否存在未过期的有效 web_session。"""
    return any(
        c.get("name") == "web_session"
        and c.get("value")
        and int(c.get("expires") or 0) > now_us
        for c in cookies
    )


async def _check_target_or_daily(browser_client, cookie_reader, profile_dir: str, now_us: int) -> dict | None:
    """【R 批 v1.4 已下线留档】旧链路 ②：目标 Chrome（独立 Profile）真实校验。

    不再被 _require_real_login 调用（R 批改走引擎独立 profile 单链路）。
    仅保留供回溯与测试参照。
    """
    """① 目标 Chrome（独立 Profile）真实校验；非独立 Profile 模式直接校验日常 Chrome。

    返回命中结果 dict；都未命中返回 None（继续走恢复链路）。
    """
    db = _target_chrome_cookie_db(profile_dir)
    try:
        if db is not None:
            if _has_valid_web_session(cookie_reader.read_cookie_db(db), now_us):
                return {"ok": True, "code": "ok", "note": "目标 Chrome 真实登录有效"}
        elif os.environ.get("XHS_BROWSER_MODE") != "profile":
            # 非独立 Profile 模式（连日常 Chrome 采集）：目标即日常 Chrome，直接校验其 Cookie
            if _has_valid_web_session(cookie_reader.read_chrome_cookies(), now_us):
                return {"ok": True, "code": "ok", "note": "日常 Chrome 真实登录有效"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": "read_fail", "note": f"读取 Chrome 登录态失败: {str(e)[:100]}"}
    return None


async def _try_restore_daily(browser_client, cookie_reader, now_us: int) -> dict | None:
    """【R 批 v1.4 已下线留档】旧链路 ③：自动读日常 Chrome 恢复登录态。

    不再被 _require_real_login 调用（R 批改走引擎独立 profile 单链路）。
    仅保留供回溯与测试参照。
    """
    try:
        daily = cookie_reader.read_chrome_cookies()
        if _has_valid_web_session(daily, now_us):
            n = _persist_restored_cookies(daily)
            try:
                await browser_client.inject_saved_cookies_now()
            except Exception:  # noqa: BLE001
                pass  # 未连接则下次连接自动注入
            return {
                "ok": True, "code": "restored",
                "note": f"已自动读取日常 Chrome 登录态恢复（{n} 条 Cookie，账号已登录），无需扫码",
            }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False, "code": "daily_fail",
            "note": f"目标 Chrome 未登录，读取日常 Chrome 登录态失败: {str(e)[:80]}（可能需先在钥匙串授权）",
        }
    return None


async def _require_real_login() -> dict:
    """真实登录校验（采集/搜索前置，解决「纸面登录」空跑问题）。

    纸面登录态（本地 cookies.json 副本）≠ 引擎独立 profile 内真实会话有效：
    小红书 Cookie 有效期短（数小时~数天），副本可能「未过期」而真实会话早已失效，
    导致采集打开页面停在登录界面、一条都采不到。

    校验链路（R 批 v1.4 改造版：登录态 100% 只依赖引擎独立 profile，不读系统 Chrome）：
    ① 引擎 CDP（登录发生在引擎独立 profile 内，内存 Cookie 最实时）→ 有有效 web_session → OK；
    ② 无 → 拦截，提示在 app 内登录小窗完成登录（扫码/手机号/微信途径不限）。

    旧链路（方案 A/B 遗留，R 批下线留档，代码保留可回溯）：
    - ② 目标 Chrome（独立 Profile）磁盘 Cookie 库校验 → _check_target_or_daily()
    - ③ 自动读日常 Chrome（用户已登录账号）恢复 → _try_restore_daily()
    均不再使用：避免依赖用户日常 Chrome 登录态（隐私 + 「完全独立」口径）。
    """
    from src import engine_pool

    # ① 引擎 CDP：独立 profile 内存 Cookie 最实时（R 批唯一登录态来源）
    try:
        eng = await engine_pool.pool.get("default")
        mem = await eng.read_cookies()
        if any(c.get("name") == "web_session" for c in mem):
            r = await _verify_cookies_or_raise(mem, "default")
            if r.get("logged_in"):
                return {"ok": True, "code": "ok", "note": "引擎独立 profile 真实登录有效（CDP）"}
    except Exception as e:  # noqa: BLE001
        log.warning("引擎 CDP 真实校验失败（走文件链路）: %s", e)

    # ② 无有效登录态 → 拦截（R 批：不再读目标/日常 Chrome Cookie 库，登录走 app 内登录小窗）
    return {
        "ok": False, "code": "not_logged_in",
        "note": "独立 profile 无有效小红书登录态：请在 app 内登录小窗扫码/手机号/微信登录（任一途径）",
    }


def _check_license_or_raise(feature: str) -> None:
    """授权薄层闸门：业务入口统一过 check_license；free 恒放行（零影响）。

    未来档位（activation/server）切换后，未授权功能在此 403 拦截，业务代码零改动。
    """
    ok, reason, detail = lic.check_license(feature)
    if not ok:
        log.warning("license 拦截 feature=%s detail=%s", feature, detail)
        raise HTTPException(403, reason or "未授权功能")


@app.get("/api/license_status")
async def license_status():
    """授权状态（前端设置页展示：模式/功能清单/指纹摘要）。"""
    st = lic.license_status()
    return {"ok": True, "status": st}


class LicenseActivateReq(BaseModel):
    key: str


@app.post("/api/license_activate")
async def license_activate(req: LicenseActivateReq):
    """激活：RSA 验签 + 机器码绑定 + 有效期；成功后写 license.json（activation 模式即放行）。"""
    ok, reason = lic.activate(req.key)
    if not ok:
        raise HTTPException(400, reason)
    return {"ok": True, "note": "激活成功（激活码与设备绑定）", "status": lic.license_status()}


async def _require_login(account_id: str = "default"):
    """登录态熔断（每账号）：搜索/克隆/采集客户前检查登录态（30s 缓存，不反复连 Chrome）。

    ① 风控状态：该账号处于熔断/需人工（manual）时直接拦截并说明原因；
    ② 本地导入 Cookie（纸面）；
    ③ default 账号追加真实会话校验（引擎独立 profile 单链路，R 批）。
    任一明确失效即拦截并给出恢复指引。
    """
    blocked, reason = risk.is_blocked(account_id)
    if blocked:
        raise HTTPException(403, reason)
    st = await login_status()
    if st.get("status") == "not_logged_in":
        raise HTTPException(
            403,
            "小红书登录态已失效：请在 app 内登录小窗完成登录（扫码/手机号/微信任一途径）后重试",
        )
    if account_id == "default":
        real = await _require_real_login()
        if not real.get("ok"):
            raise HTTPException(403, real.get("note", "独立 profile 未登录小红书，请先在 app 内登录小窗完成登录"))


@app.post("/api/search")
async def search(req: SearchReq):
    """搜索（同步版，兼容旧前端）：采集 → 评分 → 落库。返回新增条数与最新 TOP 评分。"""
    await _require_login()
    _check_collect_throttle()  # 拟人频率保护
    before = _db().execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    try:
        res = await search_via_browser(
            req.keyword, req.limit,
            filters={
                "sort": req.sort, "note_type": req.note_type,
                "time_range": req.time_range, "scope": req.scope, "distance": req.distance,
            },
        )
        notes = res.get("items", [])
    except Exception as e:
        _mark_collect_done()
        return {"added": 0, "total": before, "top": [], "note": f"搜索未返回结果（{str(e)[:80]}），请确认 Chrome 已打开并登录小红书后重试"}
    if not notes:
        _mark_collect_done()
        return {"added": 0, "total": before, "top": [], "note": "搜索未返回结果，请确认 Chrome 已打开并登录小红书后重试"}
    notes = await _enrich_notes(notes, fast=req.fast)
    saved, total, top = _persist_notes(req.keyword, notes)
    _mark_collect_done()
    return {"added": saved, "total": total, "top": top, "source": "关键词搜索（本地 Chrome）"}


# ================= 任务式采集（开始采集 / 进度 / 停止） =================
# 采集与展示分离：采集是后台异步任务（滚动平台搜索页逐批提取，支持中途停止），
# 采满目标数 / 平台不再增长 / 用户停止 之后统一落库并评分排序，前端再展示。
# 这样避免"边采边排"导致的列表跳变（先采集后排序，样本固定排序才稳定）。
import asyncio
import uuid

_COLLECT_TASKS: dict = {}


def _rank_and_insert(con, n: dict, keyword: str, now: str, account_id: str = "default") -> bool:
    """单条笔记评分 + 落库（带账号维度）。返回是否成功。"""
    try:
        n["keyword"] = keyword
        n["local_cover"] = ""
        try:
            si = rank_notes([n])[0]["score_info"]
        except Exception:  # noqa: BLE001
            si = {"score": 0.0, "tag": "评分失败", "fan_ratio": 0.0,
                  "dimensions": {"like_fan_ratio": 0.0, "low_fan": 0.0,
                                 "interaction_health": 0.0, "freshness": 0.0}}
        con.execute(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                n.get("feed_id"), keyword, n.get("title", ""), n.get("type", ""),
                n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
                n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""),
                n.get("ip_location", ""),
                _to_int(n.get("liked_count")), _to_int(n.get("collected_count")),
                _to_int(n.get("comment_count")), _to_int(n.get("shared_count")),
                _to_int(n.get("fans_count")), n.get("publish_time"),
                si["score"], si["tag"], si["fan_ratio"],
                json.dumps(si, ensure_ascii=False), now, n.get("video_url", ""),
                account_id or "default",
            ),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _cleanup_orphan_images() -> int:
    """清理无引用本地图片：当前 notes 批次（feed_id 前缀）保留，其余残留删除。返回删除数。

    背景：封面图下载链路已废弃（前端封面走 cover_url 网络直连，local_cover 从未写入），
    采集/克隆的旧图会无限堆积——启动时（后端 startup + 前端启动请求）双保险清理。"""
    ids = _referenced_feed_ids()
    if not IMAGES_DIR.exists():
        return 0
    removed = 0
    for f in IMAGES_DIR.iterdir():
        if _keep_image(f, ids):
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:  # noqa: BLE001
            pass
    return removed


def _referenced_feed_ids() -> set:
    """当前 notes 批次引用的 feed_id 集合（DB 读取失败兜底空集=全清理）。"""
    try:
        con = _db()
        ids = {r[0].replace("#", "_") for r in con.execute("SELECT feed_id FROM notes").fetchall()}
        con.close()
        return ids
    except Exception:  # noqa: BLE001
        return set()


def _keep_image(f, ids: set) -> bool:
    """是否保留该图片文件：非文件 / 用户上传资产（user_ 前缀）/ 当前批次引用。"""
    if not f.is_file():
        return True
    # 用户发布页「追加图片」上传的资产（user_ 前缀）属于用户主动操作产物，永不自动清理
    if f.name.startswith("user_"):
        return True
    feed_id = f.name.split("_", 1)[0] if "_" in f.name else f.stem
    return feed_id in ids


def _persist_notes(keyword: str, notes: list, account_id: str = "default") -> tuple:
    """搜索/采集结果落库：新结果替换旧批次（用完即失效）+ 本地评分。
    返回 (added, total, top)。"""
    con = _db()
    con.execute("DELETE FROM notes")
    con.commit()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    saved = 0
    for n in notes:
        if _rank_and_insert(con, n, keyword, now, account_id):
            saved += 1
    con.commit()

    top = con.execute(
        "SELECT feed_id, title, nickname, liked_count, fans_count, score, tag, cover_url "
        "FROM notes ORDER BY score DESC LIMIT 10"
    ).fetchall()
    total = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    return saved, total, [dict(r) for r in top]


async def _enrich_notes(notes: list, fast: bool = False, account_id: str = "default") -> list:
    """详情补全：互动数据（赞/藏/评/转/发布时间/作者 uid）+ 作者粉丝数（按 uid 缓存）。

    搜索卡片只有点赞粗值；详情页 SSR 提供全量互动与作者 uid，作者主页提供粉丝数
    ——三者对齐后评分才不失真（低粉爆文判断的前提）。

    fast=True 快速档：直连浏览器同页连续导航（无拟人 sleep、渲染等待缩短），20 条约
    5 分钟 → 2 分钟；fast=False 拟人串行（默认，安全优先，保持真人浏览节奏）。
    account_id：使用哪个账号引擎补全详情（默认 default）。
    """
    cache: dict = {}
    for n in notes:
        try:
            if fast:
                await _enrich_fast(n, cache, account_id)
            else:
                n = await get_feed_detail(n, account_id)
                n = await get_user_profile(n, cache, account_id)
        except Exception:  # noqa: BLE001 单条失败不影响整体
            continue
    return notes


async def _enrich_fast(n: dict, cache: dict, account_id: str = "default") -> None:
    """快速档：直连详情页补互动 + 作者主页补粉丝（无拟人等待）。"""
    d = await browser_client.note(n.get("feed_id", ""), n.get("xsec_token", ""),
                                  wait_ms=2500, account_id=account_id)
    for k in ("liked_count", "collected_count", "comment_count", "shared_count", "user_id", "author_nick"):
        if d.get(k):
            n[k] = d[k]
    if d.get("publish_time_ms"):
        n["publish_time"] = int(d["publish_time_ms"]) // 1000
    uid = n.get("user_id")
    if uid:
        await _fill_fans(n, uid, cache, account_id)


async def _fill_fans(n: dict, uid: str, cache: dict, account_id: str = "default") -> None:
    """作者粉丝数（按 uid 缓存；作者主页失败兜底 0）。"""
    if uid not in cache:
        try:
            u = await browser_client.user(uid, wait_ms=3500, account_id=account_id)
            cache[uid] = u.get("fans", "") or "0"
        except Exception:  # noqa: BLE001
            cache[uid] = "0"
    n["fans_count"] = cache[uid]


@app.post("/api/collect_start")
async def collect_start(req: SearchReq):
    """启动采集任务：后台滚动平台搜索页逐批提取，直到满额/平台不增长/用户停止。

    account_id 指定使用哪个账号引擎（空 = default）；多账户下采集号/发布号分离。
    """
    account_id = (req.account_id or "default").strip() or "default"
    _check_license_or_raise("collect")  # 授权薄层：collect 功能闸门（free 恒放行）
    await _require_login(account_id)
    _check_collect_throttle(account_id)  # 每账号拟人频率保护
    # 任务唯一键防重复（现象 2）：同 keyword+account_id 在 60s 内（含刚结束）不允许重启，
    # 防止「搜索等动作反复执行」被平台判定异常行为
    dedup_key = f"{req.keyword.strip()}|{account_id}"
    _now = time.time()
    for _tid, _st in list(_COLLECT_TASKS.items()):
        if _st.get("dedup_key") == dedup_key and _now - _st.get("started_ts", 0) < 60:
            raise HTTPException(409, "同一关键词采集任务进行中或刚结束，请稍候再试（防重复操作触发风控）")
    for tid, st in list(_COLLECT_TASKS.items()):
        if st.get("status") == "running":
            st["stop_event"] = True
    tid = uuid.uuid4().hex[:12]
    state = {
        "status": "running", "keyword": req.keyword, "limit": req.limit,
        "collected": 0, "note": "", "stop_event": False,
        "account_id": account_id, "dedup_key": dedup_key, "started_ts": _now,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _COLLECT_TASKS[tid] = state

    async def worker():
        try:
            res = await search_via_browser(
                req.keyword, req.limit,
                on_progress=lambda n: state.__setitem__("collected", n),
                should_stop=lambda: state["stop_event"],
                filters={
                    "sort": req.sort, "note_type": req.note_type,
                    "time_range": req.time_range, "scope": req.scope, "distance": req.distance,
                },
                account_id=account_id,
            )
            items = res["items"]
            state["collected"] = len(items)
            items = await _enrich_notes(items, fast=req.fast, account_id=account_id)
            saved, total, top = _persist_notes(req.keyword, items, account_id)
            if res.get("stopped"):
                state["status"] = "stopped"
                state["note"] = f"已停止：采集到 {len(items)} 条并完成排序" if len(items) else "已停止（未采到结果）"
            elif len(items) >= req.limit:
                state["status"] = "done"
                state["note"] = f"采集完成：{len(items)} 条已按评分排序"
            else:
                state["status"] = "done"
                state["note"] = f"采集完成：平台仅加载 {len(items)} 条（不足 {req.limit}），已按评分排序"
        except Exception as e:
            state["status"] = "error"
            state["note"] = f"采集失败：{str(e)[:100]}"
        finally:
            _mark_collect_done(account_id)  # 结束即记录，供节流闸使用

    state["task"] = asyncio.create_task(worker())
    return {"ok": True, "task_id": tid, "keyword": req.keyword, "limit": req.limit,
            "account_id": account_id}


@app.get("/api/status_now")
async def status_now():
    """全局状态提示条（R 批 v1.4）：实时播报「正在干什么」，不落盘。

    数据源：采集任务注册表（_COLLECT_TASKS）→ 进行中则聚合动作文案+进度；否则空闲。
    与审计日志（logger.py 落盘账本）区分：本接口只服务前端实时播报。
    """
    running = [st for st in _COLLECT_TASKS.values() if st.get("status") == "running"]
    if running:
        st = running[0]
        return {
            "action": "正在采集",
            "detail": st.get("note") or "",
            "collected": st.get("collected", 0),
            "limit": st.get("limit", 0),
            "keyword": st.get("keyword", ""),
            "collecting": True,
        }
    return {"action": "空闲", "detail": "", "collecting": False}


@app.get("/api/collect_progress")
def collect_progress(task_id: str = ""):
    st = _COLLECT_TASKS.get(task_id)
    if not st:
        return {"ok": False, "note": "任务不存在或已过期"}
    return {
        "ok": True, "task_id": task_id, "status": st["status"],
        "keyword": st["keyword"], "limit": st["limit"],
        "collected": st["collected"], "note": st["note"],
        "account_id": st.get("account_id", "default"),
    }


class CollectStopReq(BaseModel):
    task_id: str


@app.post("/api/collect_stop")
async def collect_stop(req: CollectStopReq):
    st = _COLLECT_TASKS.get(req.task_id)
    if st and st.get("status") == "running":
        st["stop_event"] = True
        return {"ok": True, "note": "停止已请求，正在收尾…",
                "account_id": st.get("account_id", "default")}
    return {"ok": False, "note": "任务不存在或已结束"}


# ================= 笔记列表 =================
@app.get("/api/notes")
def notes(keyword: str = "", order: str = "score", tag: str = ""):
    sql, params = _notes_query(keyword, tag, order)
    rows = _db().execute(sql, params).fetchall()
    out = [_decorate_note(dict(r)) for r in rows]
    return {"total": len(out), "notes": out}


def _notes_query(keyword: str, tag: str, order: str) -> tuple:
    """按关键词/标签/排序构造查询（关键词命中标题/作者/采集词）。"""
    sql = "SELECT * FROM notes WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (keyword LIKE ? OR title LIKE ? OR nickname LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if tag:
        sql += " AND tag = ?"
        params.append(tag)
    if order == "score":
        sql += " ORDER BY score DESC"
    else:
        sql += " ORDER BY fetched_at DESC"
    sql += " LIMIT 200"
    return sql, params


def _decorate_note(d: dict) -> dict:
    """补评分明细（库值优先、老数据重算兜底）与本地封面路径。"""
    _attach_score_info(d)
    cf = IMAGES_DIR / _img_name(d["feed_id"])
    d["local_cover"] = f"/app/images/{_img_name(d['feed_id'])}" if cf.exists() else ""
    return d


def _attach_score_info(d: dict) -> None:
    """评分明细：优先读库 score_info JSON；无列值则用现有字段重算兜底。"""
    si = None
    raw = d.get("score_info")
    if raw:
        try:
            si = json.loads(raw)
        except Exception:  # noqa: BLE001
            si = None
    if not si:
        try:
            si = rank_notes([d])[0]["score_info"]
        except Exception:  # noqa: BLE001
            si = None
    if si:
        d["score_info"] = si
    else:
        d.pop("score_info", None)


# ================= 作者价值聚合（P2：帮用户识别值得模仿的博主） =================
@app.get("/api/authors")
def authors():
    """从当前采集集按作者聚合：低粉爆款数越多、均分越高 → 越值得模仿。

    authors 列表字段：user_id / nickname / avatar / fans_count / note_count /
    hot_count（低粉爆款🔥篇数）/ low_count（低粉及低粉爆款篇数）/ avg_score /
    max_score / total_liked / last_publish。
    """
    con = _db()
    rows = con.execute(
        """
        SELECT
          n.user_id,
          (SELECT nickname FROM notes n2 WHERE n2.user_id = n.user_id ORDER BY score DESC LIMIT 1) AS nickname,
          (SELECT avatar   FROM notes n2 WHERE n2.user_id = n.user_id ORDER BY score DESC LIMIT 1) AS avatar,
          MAX(n.fans_count) AS fans_count,
          COUNT(*) AS note_count,
          COALESCE(SUM(CASE WHEN n.tag = '低粉爆款🔥' THEN 1 ELSE 0 END), 0) AS hot_count,
          COALESCE(SUM(CASE WHEN n.tag LIKE '低粉%' THEN 1 ELSE 0 END), 0) AS low_count,
          ROUND(AVG(n.score), 1) AS avg_score,
          MAX(n.score) AS max_score,
          COALESCE(SUM(n.liked_count), 0) AS total_liked,
          MAX(n.publish_time) AS last_publish
        FROM notes n
        WHERE n.user_id IS NOT NULL AND n.user_id != ''
        GROUP BY n.user_id
        ORDER BY hot_count DESC, avg_score DESC, note_count DESC
        """
    ).fetchall()
    out = [dict(r) for r in rows]
    total_notes = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    return {"total": len(out), "total_notes": total_notes, "authors": out}


# ================= 下载图片包（P3） =================
class DownloadImgsReq(BaseModel):
    feed_ids: list[str]


@app.post("/api/download_images")
def download_images(req: DownloadImgsReq):
    """打包选中笔记的本地图片为 zip。

    优先打包本地缓存（克隆时下载的 fid_pN.jpg / fid_cover.jpg 全部命中）；
    本地无缓存时回退远程 cover_url 补下载（带 Referer，低频，仅用户主动点打包时触发）；
    仍缺失的 feed_id 在响应头 X-Missing 中返回（前端据此提示）。
    """
    if not req.feed_ids:
        raise HTTPException(400, "没有选中的笔记")
    missing: list[str] = []
    con = _db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in req.feed_ids:
            prefix = fid.replace("#", "_")
            files = sorted(IMAGES_DIR.glob(f"{prefix}_*.jpg")) if IMAGES_DIR.exists() else []
            if files:
                for f in files:
                    zf.write(f, arcname=f.name)
                continue
            # 本地无缓存：回退远程 cover_url 补下载（与克隆下载同策略）
            row = con.execute("SELECT cover_url FROM notes WHERE feed_id = ?", (fid,)).fetchone()
            url = (row["cover_url"] if row else "") or ""
            if url:
                try:
                    reqh = urllib.request.Request(
                        url,
                        headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
                    )
                    data = urllib.request.urlopen(reqh, timeout=20).read()
                    if len(data) > 4096:  # 过滤装饰性小图
                        fname = _img_name(fid, suffix="cover")
                        (IMAGES_DIR / fname).write_bytes(data)
                        zf.write(IMAGES_DIR / fname, arcname=fname)
                        continue
                except Exception:  # noqa: BLE001
                    pass
            missing.append(fid)
    data = buf.getvalue()
    fname = f"xhs_images_{int(time.time() * 1000)}.zip"
    headers = {"Content-Disposition": f"attachment; filename={fname}"}
    resp = StreamingResponse(iter([data]), media_type="application/zip", headers=headers)
    resp.headers["X-Missing"] = json.dumps(missing)
    return resp


# ================= 发布页追加图片 =================
@app.post("/api/upload_image")
async def upload_image(file: UploadFile = File(...)):
    """发布编辑页「追加图片」：本地图片上传到 images 目录，返回代理路径（持久、可预览）。

    校验：≤15MB、仅图片扩展名（jpg/jpeg/png/webp/gif）。
    """
    try:
        data = await file.read()
    except Exception:  # noqa: BLE001
        return {"ok": False, "note": "读取上传文件失败"}
    if len(data) > 15 * 1024 * 1024:
        return {"ok": False, "note": "图片过大（≤15MB）"}
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return {"ok": False, "note": "仅支持 jpg/png/webp/gif"}
    fname = f"user_{int(time.time() * 1000)}_{random.randint(1000, 9999)}{ext}"
    (IMAGES_DIR / fname).write_bytes(data)
    return {"ok": True, "path": f"/app/images/{fname}", "filename": fname}


# ================= 单条详情 =================
@app.get("/api/note/{feed_id}")
async def note_detail(feed_id: str):
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)
    # 尝试用 token 拉最新详情（含图片列表）——本地浏览器 DOM 提取
    try:
        detail_note = dict(n)
        detail_note = await get_feed_detail(detail_note)
        imgs = detail_note.get("detail_imgs") or []
        if imgs:
            n["images"] = imgs
        if detail_note.get("desc"):
            n["desc"] = detail_note.get("desc")
        if detail_note.get("title"):
            n["title"] = detail_note.get("title")
    except Exception:
        pass
    return n


# ================= 一键克隆 =================
class CloneReq(BaseModel):
    feed_id: str


@app.post("/api/clone")
async def clone(req: CloneReq):
    """克隆：取详情 → 下载图片（视频另下载原视频）到本地 → 返回可编辑载荷。
    图文：详情图片列表 → 本地下载 → 可编辑发布。
    视频：详情原视频下载到本地（读操作，不上传平台）；小红书视频上传通道为 P 级待办，
    发布时由用户在创作页手动上传/或后续补通道，克隆本身止于「本地拿到原视频」。
    """
    _check_license_or_raise("clone")  # 授权薄层：clone 功能闸门（free 恒放行）
    await _require_login()
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)
    is_video = (n.get("note_type") or "").lower() == "video"

    images = await _collect_images(n, req.feed_id)
    detail_ok = len(images) > 0
    local_images = _download_images(req.feed_id, images)

    n["images_local"] = local_images
    n["detail_ok"] = detail_ok

    # 视频克隆：下载原视频到 data/videos（本地，带 Referer 防 403）
    video_file = ""
    video_url = (n.get("video_url") or "").strip()
    if is_video:
        if not video_url:
            # 笔记记录未带视频地址（老库/未回写）→ 拉一次详情回填
            try:
                dn = await get_feed_detail(n)
                video_url = (dn.get("video_url") or "").strip()
                n["video_url"] = video_url
                con.execute("UPDATE notes SET video_url=? WHERE feed_id=?", (video_url, req.feed_id))
                con.commit()
            except Exception:  # noqa: BLE001
                video_url = ""
        video_file = _download_video(req.feed_id, video_url)
        n["video_file"] = video_file

    return n


def _download_video(feed_id: str, url: str) -> str:
    """克隆用：下载原视频到 data/videos。返回可访问代理路径（失败/无地址返回空串）。"""
    if not url:
        return ""
    fname = f"{feed_id.replace('#', '_')}.mp4"
    dest = VIDEOS_DIR / fname
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            reqh = urllib.request.Request(
                url,
                headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(reqh, timeout=60) as resp:
                dest.write_bytes(resp.read())
        if dest.exists() and dest.stat().st_size < 1024:
            dest.unlink()
            return ""
        return f"/videos/{fname}"
    except Exception:  # noqa: BLE001
        return ""


async def _collect_images(n: dict, feed_id: str) -> list:
    """克隆用：取详情图片列表（本地浏览器提取，失败回退封面）。返回图片 URL 列表。"""
    images = []
    try:
        dn = dict(n)
        dn = await get_feed_detail(dn)
        imgs = dn.get("detail_imgs") or []
        if imgs:
            images = imgs
            if dn.get("desc"):
                n["desc"] = dn.get("desc")
            if dn.get("title"):
                n["title"] = dn.get("title")
    except Exception:  # noqa: BLE001
        pass
    if not images and n.get("cover_url"):
        images = [n["cover_url"]]
    return images


def _download_image(feed_id: str, idx: int, url: str) -> str:
    """克隆用：下载单张图到本地。返回可访问路径（失败/装饰性小图返回空串）。"""
    fname = _img_name(feed_id, suffix=f"p{idx}")
    dest = IMAGES_DIR / fname
    try:
        if not dest.exists():
            reqh = urllib.request.Request(
                url,
                headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(reqh, timeout=20) as resp:
                dest.write_bytes(resp.read())
        # 装饰性小图（头像/图标 2-4KB）兜底过滤：正文原图一般 ≥50KB，<20KB 判定为非正文图
        if dest.exists() and dest.stat().st_size < 20 * 1024:
            dest.unlink()
            return ""
        return f"/app/images/{fname}"
    except Exception:  # noqa: BLE001
        return ""


def _download_images(feed_id: str, images: list) -> list:
    """克隆用：并行下载图片（≤9 张）。返回本地路径列表。"""
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda p: _download_image(feed_id, p[0], p[1]), enumerate(images[:9])))


# ================= 采集客户（P4） =================
class ClientsReq(BaseModel):
    feed_id: str
    xsec_token: str = ""


@app.post("/api/clients")
async def collect_clients(req: ClientsReq):
    """采集笔记评论区互动用户名单（O1-3）：真实浏览器打开原作品页提取评论用户（含属地/时间），
    意向分级（需求词命中法）后入库去重。

    图文与视频笔记均支持（评论区都有互动用户）。每次采集访问一次真实页面，频次受浏览器全局锁约束。
    风控处理：触发验证码/风险页时返回 429（已采部分保留，前端提示停用 ≥24h，绝不自动重试）。
    """
    await _require_login()
    try:
        from src import browser_client
        # xsec_token 兜底：调用方未传时从本地库自动补查（explore/{id} 直链会 404，必须带 token）
        token = req.xsec_token
        if not token:
            con = _db()
            row = con.execute("SELECT xsec_token FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
            con.close()
            if row and row[0]:
                token = row[0]
        users = await browser_client.clients(req.feed_id, token)
    except HTTPException as e:
        # 风控风险页：已采部分保留（历史入库记录不动），明确提示停用窗口，不自动重试
        raise HTTPException(429, "触发平台风险拦截，建议停用 ≥24h，人工确认后再恢复采集") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"浏览器采集失败：{e}") from e
    from src.intent import grade_batch
    grade_batch(users)
    con = _db()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    added = 0
    for u in users:
        cur = con.execute(
            "INSERT OR IGNORE INTO clients "
            "(uid, feed_id, nickname, comment, profile, created_at, intent, matched_word, ip_location, comment_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                u.get("uid", ""), req.feed_id, u.get("nickname", ""), u.get("comment", ""),
                u.get("profile", ""), ts, u.get("intent", "low"), u.get("matched_word", ""),
                u.get("ip_location", ""), u.get("comment_time", ""),
            ),
        )
        added += cur.rowcount
    con.commit()
    from src.intent import INTENT_LABEL
    summary = {}
    for u in users:
        label = INTENT_LABEL.get(u.get("intent"), "低意向")
        summary[label] = summary.get(label, 0) + 1
    return {"total": len(users), "new": added, "users": users, "summary": summary}


# ================= 客户库（P4 价值视图）：已采集互动用户按足迹聚合 =================
@app.get("/api/clients_list")
def clients_list(feed_id: str = "", intent: str = ""):
    """查询客户库：按用户聚合互动足迹（评论次数多 + 关联笔记多 = 更活跃、更有价值）。
    附带意向分级（最高档位 + 属地 + 命中词）。

    可选 feed_id 只看某笔记的互动用户；intent 过滤档位；默认返回全部（按意向档位+评论次数 desc）。"""
    con = _db()
    where = []
    params = []
    if feed_id:
        where.append("feed_id = ?")
        params.append(feed_id)
    if intent:
        where.append("intent = ?")
        params.append(intent)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # 档位数值（CASE 映射）：高4 > 上3 > 中2 > 低1；每人取最高档位（同档取最新）
    intent_rank_sql = (
        "CASE intent WHEN 'high' THEN 4 WHEN 'upper' THEN 3 WHEN 'middle' THEN 2 ELSE 1 END"
    )
    sql = (
        "SELECT uid, MAX(nickname) AS nickname, COUNT(*) AS comment_count, "
        "COUNT(DISTINCT feed_id) AS note_count, "
        "(SELECT comment FROM clients c2 WHERE c2.uid = clients.uid ORDER BY created_at DESC LIMIT 1) AS sample_comment, "
        "MAX(profile) AS profile, MAX(created_at) AS created_at, "
        "(SELECT intent FROM clients c6 WHERE c6.uid = clients.uid "
        " ORDER BY " + intent_rank_sql + " DESC, c6.created_at DESC LIMIT 1) AS intent, "
        "(SELECT matched_word FROM clients c7 WHERE c7.uid = clients.uid "
        " ORDER BY " + intent_rank_sql + " DESC, c7.created_at DESC LIMIT 1) AS matched_word, "
        "(SELECT ip_location FROM clients c4 WHERE c4.uid = clients.uid ORDER BY created_at DESC LIMIT 1) AS ip_location, "
        "(SELECT comment_time FROM clients c5 WHERE c5.uid = clients.uid ORDER BY created_at DESC LIMIT 1) AS comment_time "
        "FROM clients" + where_sql + " GROUP BY uid"
    )
    rows = con.execute(sql, params).fetchall()
    from src.intent import intent_rank
    ordered = sorted(
        rows, key=lambda r: (intent_rank(r["intent"]), r["comment_count"], r["note_count"]), reverse=True
    )
    ordered = ordered[:200]
    return {"total": len(ordered), "clients": [dict(r) for r in ordered]}


# ================= 客户库聚合（O1-4）：线索/意向分布/属地TOP/近7天/留联系方式 =================
@app.get("/api/clients_agg")
def clients_agg():
    """数据聚合：线索总数 / 意向分布（四档计数）/ 属地 TOP / 近 7 天产出 / 留联系方式命中。

    与 /api/clients_list 同一数据源，聚合数字与明细一致（验收标准）。
    """
    con = _db()
    from src.intent import INTENT_LABEL, intent_rank, is_contact
    rows = con.execute(
        "SELECT intent, matched_word, comment, ip_location, created_at FROM clients"
    ).fetchall()
    # 意向分布（按档位数值排序输出：高→上→中→低）
    dist = {k: 0 for k in ("high", "upper", "middle", "low")}
    ip_counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}
    contact = 0
    for r in rows:
        it = r["intent"] or "low"
        dist[it] = dist.get(it, 0) + 1
        ip = (r["ip_location"] or "").strip()
        if ip:
            ip_counts[ip] = ip_counts.get(ip, 0) + 1
        d = (r["created_at"] or "")[:10]
        if d:
            day_counts[d] = day_counts.get(d, 0) + 1
        if is_contact(r["comment"] or "", r["matched_word"] or ""):
            contact += 1
    intent_dist = [
        {"intent": k, "label": INTENT_LABEL[k], "count": dist[k]}
        for k in sorted(dist, key=intent_rank, reverse=True)
    ]
    ip_top = [{"ip": k, "count": v} for k, v in sorted(ip_counts.items(), key=lambda x: -x[1])[:6]]
    # 近 7 天时间线：含今天往前 7 天，无数据的天补 0
    days = []
    today = datetime.date.today()
    for i in range(6, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        days.append({"date": d, "count": day_counts.get(d, 0)})
    return {
        "total": len(rows),
        "intent_dist": intent_dist,
        "ip_top": ip_top,
        "week": days,
        "contact": contact,
    }


# ================= Cookie（显示 / 一键从 Chrome 导入） =================
_COOKIE_ORDER = ["web_session", "last_web_session", "a1", "webId", "gid", "id_token", "acw_tc", "websectiga"]


def _cookie_items(cookies: list) -> tuple[list, str]:
    """把本地 cookie 列表整理成展示项（按 _COOKIE_ORDER 排序）+ 提取 web_session 摘要。"""
    items = []
    for i, nm in enumerate(c.get("name", "") for c in cookies):
        items.append({"name": nm, "value": "", "domain": ".xiaohongshu.com", "ord": i})
    items.sort(key=lambda x: (_COOKIE_ORDER.index(x["name"]) if x["name"] in _COOKIE_ORDER else 99, x["name"]))
    ws = next((c.get("value", "") for c in cookies if c.get("name") == "web_session"), "") or ""
    return items, ws


@app.get("/api/cookie_info")
async def cookie_info():
    """只读展示 Cookie 摘要：读本地 data/cookies.json，不连浏览器、不打开页面。

    原实现每次调 browser_client.cookies() → 内部 _load(explore) 会在 Chrome 打开小红书
    首页 tab 再关闭——APP 每次刷新都触发「小红书 tab 闪开又消失」（用户痛点）。改为纯本地读取。"""
    try:
        p = DATA_DIR / "cookies.json"
        data = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf8"))
        cookies = data.get("cookies", []) or []
        items, ws = _cookie_items(cookies)
        return {
            "ok": True,
            "count": len(cookies),
            "saved_at": data.get("saved_at", ""),
            "account": (ws[:8] + "…") if ws else "",
            "cookies": items,
        }
    except Exception as e:
        return {"ok": False, "count": 0, "saved_at": "", "cookies": [], "error": f"本地读取失败: {str(e)[:100]}"}


class ImportCookieReq(BaseModel):
    cookie_text: str = ""


def _parse_cookie_text(text: str) -> list[dict]:
    """解析导入的 Cookie 文本：支持 `k=v; k2=v2` 与每行一条 name=value。
    返回 Playwright/CDP 可用的 cookie 列表（域固定小红书）。"""
    out = []
    for seg in text.replace("\n", ";").split(";"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        k, v = seg.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        out.append({
            "name": k,
            "value": v,
            "domain": ".xiaohongshu.com",
            "path": "/",
            "secure": True,
            "httpOnly": k in ("web_session", "last_web_session", "a1", "acw_tc"),
            "sameSite": "Lax",
        })
    return out


def _save_cookies_file(cookies: list[dict]):
    """备份旧文件后保存 cookies.json（供 profile 模式启动自动载入）。"""
    dest = DATA_DIR / "cookies.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.replace(dest.with_suffix(".json.bak"))
    dest.write_text(json.dumps(
        {"cookies": cookies, "imported_at": datetime.datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2,
    ))


def _read_chrome_cookies() -> tuple[list | None, dict | None]:
    """【R 批 v1.4 已下线留档】读 .xhs-leads-profile 的 Chrome Cookie 库（钥匙串解密）。

    不再被任何正式接口调用（refresh_cookie_from_chrome 已改走引擎独立 profile 单链路）。
    仅保留供回溯与测试参照；返回 (cookies, 错误响应)；成功时错误为 None。
    """
    profile_dir = os.environ.get("XHS_PROFILE_DIR") or os.path.join(
        os.path.expanduser("~"), ".xhs-leads-profile"
    )
    os.environ["XHS_CHROME_USER_DATA_DIR"] = profile_dir
    from src import cookie_reader

    try:
        return cookie_reader.read_chrome_cookies(), None
    except cookie_reader.ChromeCookieReadError as e:
        status = cookie_reader.keychain_status()
        if status == "pending":
            return None, {
                "ok": False,
                "need_auth": True,
                "error": "首次自动读取需要授权：请在弹出的系统钥匙串窗口中输入密码并点「始终允许」，然后重试。",
            }
        return None, {"ok": False, "need_auth": False, "error": f"读取 Chrome 登录态失败：{str(e)[:120]}"}


@app.post("/api/refresh_cookie_from_chrome")
async def refresh_cookie_from_chrome(account_id: str = "default"):
    """登录态自动续期（R 批 v1.4：引擎独立 profile 单链路，不再读系统 Chrome）。

    ① 引擎 CDP：登录发生在引擎独立 profile 内，内存 Cookie 立即可读（扫码/手机号/
       微信登录后即生效），成功后 risk.resolve(account_id) —— 软风控自动解锁；
    ② 引擎未运行/未登录 → 返回引导：在 app 内登录小窗完成登录（R3）。

    旧链路（方案 A 遗留，R 批下线留档）：读 .xhs-leads-profile/引擎 profile 磁盘
    Cookie 库（钥匙串解密）自动续命 → 不再使用（依赖系统 Chrome 登录态，违背独立口径）。
    """
    from src import engine_pool, risk

    account_id = account_id or "default"
    # ① 引擎内存 Cookie（CDP，登录后立即可用）
    try:
        eng = await engine_pool.pool.get(account_id)
        mem = await eng.read_cookies()
        if any(c.get("name") == "web_session" for c in mem):
            r = await _verify_cookies_or_raise(mem, account_id)
            if r.get("logged_in"):
                _save_cookies_file(mem)
                risk.resolve(account_id, reason="引擎登录恢复（CDP）")
                return {
                    "ok": True, "valid": True, "count": len(mem),
                    "account": "已登录（引擎）",
                    "raw": f"url={r.get('url')} hasLoginUi={r.get('hasLoginUi')}",
                }
    except Exception as e:  # noqa: BLE001
        log.warning("引擎 CDP 读 Cookie 失败: %s", e)

    # ② 引擎未登录 → 引导 app 内登录（R 批：不再读系统/目标 Chrome 磁盘库自动续命）
    return {
        "ok": False, "valid": False,
        "error": "独立 profile 无有效登录态：请在 app 内登录小窗扫码/手机号/微信登录（任一途径）后重试。",
    }


async def _verify_cookies_or_raise(cookies: list, account_id: str = "default") -> dict:
    """校验导入/读取的 Cookie（headless 优先、回退账号引擎），失败抛 502。

    引擎生命周期由 shutdown 钩子统一管理（不再"用后即停"，防止中断引擎登录态）。
    """
    from src import browser_client

    try:
        return await browser_client.verify_cookies(cookies, account_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"校验失败（浏览器未就绪）：{str(e)[:100]}")


@app.post("/api/import_cookie")
async def import_cookie(req: ImportCookieReq):
    """导入 Cookie（新架构）：解析 → 注入独立 profile Chrome 校验登录态 → 有效则保存到本地。

    - daily 调试模式：校验在独立 profile Chrome（APP 交付形态预演）中完成，不污染用户日常 Chrome；
    - 生产模式：APP 自拉独立 profile，导入后直接注入该 Chrome，采集/发布即用该账号 —— 链路相同。
    - 校验失败（无效/过期）：返回原因，前端引导去 Chrome 扫码登录。
    """
    text = (req.cookie_text or "").strip()
    if not text:
        raise HTTPException(400, "请粘贴需要导入的 Cookie（name=value; 格式）")
    cookies = _parse_cookie_text(text)
    if not cookies:
        raise HTTPException(400, "未能从文本中解析出 Cookie（请确认格式：web_session=xxx; a1=yyy）")

    r = await _verify_cookies_or_raise(cookies)
    if not r.get("logged_in"):
        return {
            "ok": False,
            "valid": False,
            "error": "Cookie 无效或已过期（登录态检测未通过）。请确认 Cookie 来自最近登录的小红书账号，或直接在 app 内登录小窗扫码/手机号/微信登录。",
            "count": len(cookies),
            "raw": f"url={r.get('url')} hasLoginUi={r.get('hasLoginUi')}",
        }

    # 有效：备份旧文件后保存（供 profile 模式启动自动载入）
    _save_cookies_file(cookies)
    ws = next((c["value"] for c in cookies if c["name"] == "web_session"), "")
    return {
        "ok": True,
        "valid": True,
        "count": len(cookies),
        "account": (ws[:8] + "…") if ws else "已登录",
        "note": "Cookie 有效并已保存。独立引擎（app 内呈现形态）将直接使用该账号；登录态只存引擎独立 profile，不依赖系统 Chrome。",
    }


# ================= 发布 =================
class PublishReq(BaseModel):
    title: str
    content: str = ""
    desc: str = ""  # 兼容前端旧字段（apiPublish 传 desc）
    tags: list[str] = []
    visibility: str = "仅自己可见"
    images: list[str] = []
    feed_id: str = ""
    is_original: bool = False


class DraftDeleteReq(BaseModel):
    id: int


@app.post("/api/save_draft")
async def save_draft(req: PublishReq):
    """存草稿（长期保留资产；与搜索/采集结果'用完即失效'区分，草稿可随时恢复）。

    简约版：完整持久化（含图片路径 JSON）；同 feed_id 覆盖更新（不堆积重复草稿），
    无 feed_id 则新建。"""
    con = _db()
    now = _now().isoformat(timespec="seconds")
    images_json = json.dumps(req.images[:9], ensure_ascii=False)
    if req.feed_id:
        row = con.execute("SELECT id FROM drafts WHERE feed_id = ? AND status='draft'", (req.feed_id,)).fetchone()
        if row:
            con.execute(
                "UPDATE drafts SET title=?, content=?, tags=?, visibility=?, images=?, created_at=? WHERE id=?",
                (req.title[:50], (req.content or req.desc)[:1000], ",".join(req.tags), req.visibility,
                 images_json, now, row[0]),
            )
            con.commit()
            return {"ok": True, "note": "草稿已更新（同笔记覆盖旧草稿，长期保留）"}
    con.execute(
        "INSERT INTO drafts (feed_id, title, content, tags, visibility, images, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (req.feed_id or "", req.title[:50], (req.content or req.desc)[:1000], ",".join(req.tags), req.visibility,
         images_json, "draft", now),
    )
    con.commit()
    return {"ok": True, "note": "草稿已保存（长期保留，可在草稿库恢复）"}


@app.get("/api/drafts")
def list_drafts():
    """草稿库列表（最新在前）：标题/正文/话题/图片/可见范围/时间。"""
    con = _db()
    rows = con.execute(
        "SELECT id, feed_id, title, content, tags, visibility, images, created_at FROM drafts "
        "WHERE status='draft' ORDER BY id DESC LIMIT 50"
    ).fetchall()
    out = []
    for r in rows:
        imgs = []
        try:
            imgs = json.loads(r["images"] or "[]")
        except Exception:  # noqa: BLE001
            imgs = []
        out.append({
            "id": r["id"], "feed_id": r["feed_id"] or "", "title": r["title"],
            "desc": r["content"], "tags": (r["tags"] or "").split(",") if r["tags"] else [],
            "visibility": r["visibility"], "images": imgs, "created_at": r["created_at"],
        })
    return {"ok": True, "total": len(out), "drafts": out}


@app.post("/api/draft_delete")
async def draft_delete(req: DraftDeleteReq):
    """删除草稿（简约版：单条删除，无回收站）。"""
    con = _db()
    con.execute("UPDATE drafts SET status='deleted' WHERE id=?", (req.id,))
    con.commit()
    return {"ok": True, "note": "草稿已删除"}


# 发布拟人化护栏（2026-10-26 v3 修订：合理像人档，保留自动化）
#   依据 research/step5-小红书风控调研.md v3：
#   - 被抓根因=频率过高（凌晨连发+30s 重试），非"用工具即死"
#   - 合理像人：redbook 间隔 3-4h；MediaUse ≤3/h；真人创作者日更 1-3 条
#   - 不取 NoobClaw 1帖/天蜗牛档；也不取"随时连发"的危险档
#   - 所有动作过 human_delay() 随机延迟层，杜绝固定节奏
#   - 风控信号 → 硬熔断 24h（人工解锁），绝不自动重试
PUBLISH_COOLDOWN_MIN = 180          # 两次发布最小间隔（3h，redbook 下限）
PUBLISH_COOLDOWN_JITTER = 15        # 间隔随机抖动 ±15min，打散固定节奏
PUBLISH_DAILY_MAX = 2               # 每日成功发布上限（真人日更 1-3 条的保守侧）
PUBLISH_DAILY_MAX_STR = "2"         # 提示文案用
WEEKLY_MAX_DAYS = 6                 # 每周最多 6 个发布日（模拟真人每周有休息日）
ACTIVE_HOUR_START = 10              # 仅 10:00-21:30 真人活跃时段可发布
ACTIVE_HOUR_END = 21.5
DUPLICATE_WINDOW_HOURS = 24         # 同一标题+正文 24h 拒发
RISK_KEYWORDS = ("验证", "频繁", "风控", "拦截", "校验", "未跳转", "发布失败", "异常", "操作过快", "稍后再试")

# 风控熔断：最近 N 小时内出现过风控类失败 → 拒绝一切发布（硬停，需人工解锁）
RISK_BLOCK_HOURS = 24


def human_delay(lo: float = 1.0, hi: float = 3.0):
    """拟人化随机延迟：所有碰账号的动作在调用前先"思考"随机时长，杜绝固定节奏。"""
    time.sleep(random.uniform(lo, hi))


def _risk_blocked_until(con) -> str | None:
    """最近 24h 内发布失败且命中风控关键词 → 返回熔断到期时间，否则 None。"""
    since = (_now() - datetime.timedelta(hours=RISK_BLOCK_HOURS)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT raw, created_at FROM publish_logs WHERE status='failed' AND created_at>=? ORDER BY id DESC LIMIT 5",
        (since,),
    ).fetchall()
    for row in rows:
        if any(kw in (row["raw"] or "") for kw in RISK_KEYWORDS):
            until = datetime.datetime.fromisoformat(row["created_at"]) + datetime.timedelta(hours=RISK_BLOCK_HOURS)
            if until > _now():
                return until.isoformat(timespec="minutes")
    return None


def _publish_week_days(con) -> int:
    """近 7 天有成功发布的日期数（模拟每周休息日）。"""
    since = (_now() - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT COUNT(DISTINCT date(created_at)) AS c FROM publish_logs WHERE status='success' AND created_at>=?",
        (since,),
    ).fetchone()
    return row["c"] if row else 0


def _now():
    return datetime.datetime.now()


def _last_publish_ago_minutes(con) -> float | None:
    row = con.execute("SELECT created_at FROM publish_logs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    try:
        last = datetime.datetime.fromisoformat(row["created_at"])
    except (TypeError, ValueError):
        return None
    return (_now() - last).total_seconds() / 60.0


def _publish_count_today(con) -> int:
    today = _now().date().isoformat()
    row = con.execute(
        "SELECT COUNT(*) AS c FROM publish_logs WHERE status='success' AND date(created_at)=?",
        (today,),
    ).fetchone()
    return row["c"] if row else 0


def _duplicate_published(con, title, content) -> bool:
    since = (_now() - datetime.timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT COUNT(*) AS c FROM publish_logs WHERE status='success' AND title=? AND content=? AND created_at>=?",
        (title[:50], content[:200], since),
    ).fetchone()
    return (row["c"] if row else 0) > 0


def _publish_guard_time(con) -> dict | None:
    """护栏（时间类）：风控熔断 / 活跃时段 / 冷却间隔。任一不满足返回拒绝响应。"""
    blocked = _risk_blocked_until(con)
    if blocked:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"风控熔断中：近 24h 发布被平台拦截（验证码/频繁/拦截）。发布功能已自动停用至 {blocked}，请人工确认账号状态后再恢复。切勿自动重试。",
        }

    hour = _now().hour + _now().minute / 60.0
    if not (ACTIVE_HOUR_START <= hour <= ACTIVE_HOUR_END):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"当前 {_now().hour:02d}:{_now().minute:02d} 非真人活跃时段（仅 {ACTIVE_HOUR_START}:00-21:30 可发布）。真人不会半夜发笔记，请在白天发布。",
        }

    ago = _last_publish_ago_minutes(con)
    limit = PUBLISH_COOLDOWN_MIN + random.uniform(0, PUBLISH_COOLDOWN_JITTER)
    if ago is not None and ago < limit:
        wait = limit - ago
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"发布过于频繁：距上次发布仅 {ago:.0f} 分钟。平台对短时间连发很敏感（间隔 3-4 小时），请等待 {wait:.0f} 分钟后重试。",
        }
    return None


def _publish_guard_quota(con, req: PublishReq) -> dict | None:
    """护栏（配额类）：每日上限 / 每周发布日 / 文案防重。任一不满足返回拒绝响应。"""
    if _publish_count_today(con) >= PUBLISH_DAILY_MAX:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"今日发布已达上限（{PUBLISH_DAILY_MAX} 条）。真人日更 1-3 条，明天再发。",
        }

    if _publish_week_days(con) >= WEEKLY_MAX_DAYS:
        return {
            "ok": False,
            "cooldown": True,
            "raw": "本周已连续发布 6 天。真人每周会有休息日，请明天再发。",
        }

    if _duplicate_published(con, req.title, req.content or req.desc):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"该文案 {DUPLICATE_WINDOW_HOURS} 小时内已发布过。平台对重复文案判定极严，请改写标题或正文。",
        }
    return None


def _publish_guard(con, req: PublishReq) -> dict | None:
    """发布护栏链：时间类 + 配额类检查。任一不满足返回拒绝响应，否则 None。"""
    return _publish_guard_time(con) or _publish_guard_quota(con, req)


@app.post("/api/publish")
def publish(req: PublishReq):
    _check_license_or_raise("publish")  # 授权薄层：publish 功能闸门（free 恒放行）
    # ① 拟人思考：发布前随机停顿 2-5s（像真人斟酌后再点）
    human_delay(2.0, 5.0)

    con = _db()
    guard = _publish_guard(con, req)
    if guard:
        return guard

    # ⑧ 执行发布：Docker 已退役（2026-10-26 迁移：采集/登录迁到外部 Chrome）。
    #    发布永远用户手动（产品红线）：后端只做校验与载荷准备，实际发布由用户在
    #    Chrome 中手动完成（避免自动化发布触发平台风控）。载荷已随请求完整传给前端。
    import urllib.parse
    xhs_write_url = "https://www.xiaohongshu.com/write"
    ok = False
    text = "发布执行已迁移：请在 Chrome 小红书发布页手动发布（标题/正文/图片已就绪）。" \
           f"发布页: {xhs_write_url}"

    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (req.feed_id, req.title[:50], (req.content or req.desc)[:200], ",".join(req.tags), req.visibility,
         "success" if ok else "failed", _now().isoformat(timespec="seconds")),
    )
    con.commit()
    return {"ok": ok, "raw": text[:400]}


# ================= 发布记录 =================
@app.get("/api/logs")
def logs():
    con = _db()
    rows = con.execute("SELECT * FROM publish_logs ORDER BY id DESC LIMIT 50").fetchall()
    return {"logs": [dict(r) for r in rows]}


class PublishStatsReq(BaseModel):
    log_id: int
    note_url: str = ""


@app.post("/api/publish_stats")
async def publish_stats(req: PublishStatsReq):
    """数据回流：本地浏览器打开已发布笔记详情页，抓取互动数据（浏览/赞/藏/评/转）回写发布记录。

    优先用用户提供的发布后笔记链接（手动发布后新笔记有新的 feed_id）；
    未提供时回退按记录里的来源 feed_id 抓取。
    """
    await _require_login()
    con = _db()
    row = con.execute("SELECT * FROM publish_logs WHERE id=?", (req.log_id,)).fetchone()
    if not row:
        raise HTTPException(404, "发布记录不存在")
    url = (req.note_url or "").strip()
    if url:
        d = await get_note_detail_by_url(url)
        if not d:
            raise HTTPException(400, "打开笔记失败：链接无效或需登录（请确认 Chrome 已登录小红书，且链接是公开笔记）")
    else:
        feed_id = row["feed_id"] or ""
        if not feed_id:
            raise HTTPException(400, "该记录无来源笔记 ID，请粘贴已发布笔记链接后重试")
        # 搜索来源笔记带 xsec_token 才能打开详情页（explore 直开会 404/风控）
        tok = ""
        row2 = con.execute("SELECT xsec_token FROM notes WHERE feed_id=?", (feed_id,)).fetchone()
        if row2 and row2[0]:
            tok = row2[0]
        note = {"feed_id": feed_id, "xsec_token": tok}
        d = await get_feed_detail(note)
    stats = {k: d.get(k) or "" for k in ("views", "liked_count", "collected_count", "comment_count", "shared_count")}
    if not any(v for v in stats.values()):
        raise HTTPException(400, "未抓到互动数据：请确认 Chrome 已登录小红书且笔记可正常访问（笔记可能已删除/被风控）")
    stats["fetched_at"] = _now().isoformat(timespec="seconds")
    con.execute("UPDATE publish_logs SET stats_json=? WHERE id=?",
                (json.dumps(stats, ensure_ascii=False), req.log_id))
    con.commit()
    return {"ok": True, "stats": stats}


# ================= 图片代理（容器内路径 → 浏览器可读） =================
@app.get("/api/img")
def img_proxy(path: str = ""):
    """把 /app/images/<file> 映射到本机 data/images/<file> 返回图片字节。"""
    from fastapi.responses import Response
    if not path or not path.startswith("/app/images/"):
        raise HTTPException(400, "仅允许 /app/images 路径")
    fname = Path(path).name  # 只取文件名，防路径穿越
    f = IMAGES_DIR / fname
    if not f.exists():
        raise HTTPException(404, "图片不存在")
    return Response(f.read_bytes(), media_type="image/jpeg")


# ================= O1 AI 模块：LLM 配置与生成（Key 门控） =================
# 设计定稿 §3.1：未配置 Key 时前端 AI 按钮灰态、后端 AI 接口 400 拒绝。
from src import llm_service  # noqa: E402


class LLMConfigReq(BaseModel):
    api_key: str = ""
    openai_base_url: str = ""
    default_model: str = ""


@app.get("/api/llm_config")
def get_llm_config():
    """读取 AI 配置（key 掩码，不泄露明文）。"""
    return llm_service.public_config()


@app.post("/api/llm_config")
def set_llm_config(req: LLMConfigReq):
    """保存 AI 配置。api_key 为空时保留旧 key（前端可不回传明文）。"""
    if not req.openai_base_url or not req.default_model:
        raise HTTPException(400, "请填写 Base URL 与模型名称")
    return llm_service.save_config(req.api_key, req.openai_base_url, req.default_model)


@app.post("/api/llm_config/clear")
def clear_llm_config():
    """清除 AI 配置（Key/Base/模型全清）→ 回到未配置态。

    背景：save_config 空 key 保留（保护明文不回传），但用户换号/换中转站后
    无法在 UI 移除已配置 Key——本接口提供显式清除语义（E1 场景5 遗留小缺口）。
    """
    return llm_service.clear_config()


@app.post("/api/llm_test")
async def test_llm(req: LLMConfigReq):
    """测试 LLM 连接：用当前表单值（或已存配置）GET /models 验证可用性。"""
    return await llm_service.test_with_saved(req.api_key, req.openai_base_url, req.default_model)


# ================= O1 账号人设（AI 生成口吻；v1.5 设置页三卡之一） =================
# 存储：data/persona.json（本地明文，与 llm_config.json 同级信任域）。
PERSONA_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "persona.json"


class PersonaReq(BaseModel):
    industry: str = ""
    tone: str = ""
    topics: str = ""


def _load_persona() -> dict:
    return read_json_safe(PERSONA_PATH)


def _save_persona(d: dict) -> dict:
    write_json(PERSONA_PATH, d)
    return d


@app.get("/api/persona")
def get_persona():
    """读取账号人设（行业 / 口吻 / 常发主题），未配置返回空串。"""
    p = _load_persona()
    return {
        "ok": True,
        "industry": p.get("industry", ""),
        "tone": p.get("tone", ""),
        "topics": p.get("topics", ""),
        "updated_at": p.get("updated_at", ""),
    }


@app.post("/api/persona")
def set_persona(req: PersonaReq):
    """保存账号人设。空字段保留原值（前端可不重复提交）。"""
    old = _load_persona()
    cur = {
        "industry": req.industry if req.industry.strip() else old.get("industry", ""),
        "tone": req.tone if req.tone.strip() else old.get("tone", ""),
        "topics": req.topics if req.topics.strip() else old.get("topics", ""),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_persona(cur)
    return {"ok": True, **cur}


class AIGenReq(BaseModel):
    action: str = ""          # title_summarize / title_rewrite / body_create / body_rewrite / tags
    note: dict = {}           # {title, desc, keyword} 原笔记信息


_AI_PROMPTS = {
    "title_summarize": (
        "你是小红书爆文标题优化师。请把下面的笔记标题提炼成 1 个更吸引人的标题（不超过20字，"
        "不含标点符号和表情，保留原笔记的核心卖点方向）。标题可从这些公式选：数字+人群+结果、"
        "悬念提问、对比反差、场景共鸣。只输出标题本身，不要任何解释：\n标题：{title}"
    ),
    "title_rewrite": (
        "你是小红书爆文标题优化师。保留原笔记的爆款方向，重写 1 个全新的标题（不超过20字，"
        "不含标点符号和表情，标题要有钩子、引发好奇或共鸣）。标题可从这些公式选：数字+人群+结果、"
        "悬念提问、对比反差、场景共鸣。只输出标题本身，不要任何解释：\n标题：{title}"
    ),
    "body_create": (
        "你是小红书爆文作者。参考原笔记的主题方向，围绕品类关键词【{keyword}】创作一篇全新的小红书正文"
        "（800-1200字，口语化、分段清晰、适当使用emoji、结尾带 3-5 个相关话题标签）。正文按小红书爆文黄金结构："
        "①痛点/场景开头（1-2句抓注意力）②干货分点展开（可执行、带数据或对比、每段有小标题感）"
        "③价值收尾（总结+引导互动）。只输出正文，不要任何解释：\n"
        "原标题：{title}\n原正文：{desc}"
    ),
    "body_rewrite": (
        "你是小红书爆文作者。把下面的正文语义重写一遍（换一种表达方式，保留核心信息与观点，"
        "口语化、分段清晰、适当使用emoji）。保持小红书爆文结构：痛点开头→干货展开→价值收尾，"
        "避免“微改原句”式的同质化改写。只输出重写后的正文，不要任何解释：\n原正文：{desc}"
    ),
    "tags": (
        "你是小红书话题标签专家。为下面的笔记提取 5 个精准的话题标签（#开头，中文为主，"
        "覆盖品类/场景/人群/痛点；优先结合品类关键词【{keyword}】）。只输出标签列表（用空格分隔），"
        "不要任何解释：\n标题：{title}\n正文：{desc}"
    ),
}


@app.post("/api/ai_generate")
async def ai_generate(req: AIGenReq):
    """AI 文稿生成（Key 门控，O1-2 + 质量保障）：未配置 LLM 时 400 拒绝（前端按钮灰态+提示）。

    质量保障：①账号人设（行业/口吻/常发主题，data/persona.json）注入系统提示，保持人设一致；
    ②爆文黄金结构/标题公式内置于动作提示（_AI_PROMPTS），避免“通用AI味”与平庸文案。
    """
    _check_license_or_raise("llm")  # 授权薄层：llm 功能闸门（free 恒放行）
    if req.action not in _AI_PROMPTS:
        raise HTTPException(400, f"未知 AI 动作：{req.action}")
    title = (req.note.get("title") or "")[:500]
    desc = (req.note.get("desc") or "")[:2000]
    keyword = (req.note.get("keyword") or "")[:200]
    try:
        prompt = _AI_PROMPTS[req.action].format(title=title, desc=desc, keyword=keyword)
        # 人设注入：已配置账号人设时拼入系统提示（未配置退化为通用助手，行为不变）
        persona = _load_persona()
        system_parts = ["你是一个专业的小红书内容创作助手，输出精炼、可直接使用，不要客套。"]
        if persona.get("industry") or persona.get("tone") or persona.get("topics"):
            system_parts.append(
                "账号人设：行业【{}】、口吻【{}】、常发主题【{}】。生成内容保持该人设一致，"
                "避免“宝子们/家人们/谁懂啊”式通用AI味。".format(
                    persona.get("industry", ""), persona.get("tone", ""), persona.get("topics", "")
                )
            )
        result = await llm_service.chat("\n".join(system_parts), prompt)
        return {"ok": True, "action": req.action, "result": result}
    except ValueError as e:  # 未配置 Key / 请求失败 → 400
        raise HTTPException(400, str(e)) from e


# ================= 静态前端 =================
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
# 本地封面图静态服务（xhscdn 图床按 Referer 防盗链，本地代理同源访问无 403）
app.mount("/app/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
# 本地视频静态服务（视频克隆下载产物，供创作页预览/后续发布上传）
app.mount("/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="videos")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
