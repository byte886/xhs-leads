"""xhs-leads 后端壳：REST API + SQLite 读写 + 静态前端服务（engine_pool 引擎池 CDP 直连，非外部 MCP 封装）。

运行:
  uvicorn server:app --host 127.0.0.1 --port 8791

API:
  GET  /                    → 前端界面（frontend/dist/index.html）
  GET  /api/login_status    → 登录状态
  POST /api/search          → 搜索+详情补全+评分+落库（同步返回新增数）
  GET  /api/notes           → 笔记列表（评分排序，支持关键词过滤）
  POST /api/clone           → 一键克隆：拉详情+下载图片，返回可编辑载荷
  POST /api/publish         → 发布（默认仅自己可见）
  GET  /api/logs            → 发布记录
"""
import asyncio
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
from pydantic import BaseModel, ConfigDict, Field

# ---- 统一日志（审计账本） ----
from src import logger as _log
from src import shell_window as _shell_window  # noqa: E402（R-6.22 窗口校准：模块级导入，避免各函数重复延迟导入）

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
    set_rhythm,
)
from src import browser_client  # noqa: E402（快速档 _enrich_notes 直连详情/主页）
from src import injections  # noqa: E402（R-6.30a：注入脚本集中注册表）
from src import config, risk  # noqa: E402（架构改造 P1：配置中心 + 每账号风控）
from src.cdp_timeouts import CDP_QUICK, cdp_call  # noqa: E402（R-6.53s：统一 CDP 超时保护）
from src import license as lic  # noqa: E402（授权薄层：free 恒放行，业务入口统一过闸）

app = FastAPI(title="XHS Leads · 小红书获客助手")

# ---- 风控状态记录（R-6.53z3：前置频率闸已随死代码清理移除，只剩结束时间记录）----


def _mark_collect_done(account_id: str = "default") -> None:
    """平台任务结束（成功/停止/失败）统一记录结束时间，供节流闸使用。"""
    risk.mark_collect_done(account_id or "default")


def _reset_collection_data() -> int:
    """R-6.25：清空采集存储数据（笔记 + 客户 + 采集下载的本地图片）。

    语义（用户对齐）：应用重启时清空一次；每次新采集开始时再清空一次，
    保证任何时刻库里只有"本次运行/本次采集"的数据。
    user_ 前缀（发布页「追加图片」上传资产）属用户主动操作产物，保留。
    返回删除的图片文件数。
    """
    con = _db()
    con.execute("DELETE FROM notes")
    con.execute("DELETE FROM clients")
    con.commit()
    removed = 0
    if IMAGES_DIR.exists():
        for f in IMAGES_DIR.iterdir():
            if f.is_file() and not f.name.startswith("user_"):
                try:
                    f.unlink()
                    removed += 1
                except OSError:  # noqa: BLE001
                    pass
    return removed


@app.on_event("startup")
async def _startup_cleanup():
    """启动时清理：残留任务 tab + 采集存储数据。best-effort 不阻塞启动。

    仅清理「已运行」引擎的残留 tab；引擎未启动时跳过——启动清理不应有
    拉起引擎的副作用（打包态冷启动慢，dev 态回归也依赖本钩子不拉引擎）。
    """
    try:
        from src import browser_client, engine_pool
        if engine_pool.pool.peek("default") is not None:
            await asyncio.wait_for(browser_client.cleanup_tabs(), timeout=15)
    except Exception:  # noqa: BLE001
        pass
    try:
        # R-6.25：启动即清空采集存储（重启后库里只有本次运行数据）
        _reset_collection_data()
    except Exception:  # noqa: BLE001
        pass


@app.on_event("shutdown")
async def _shutdown_close_engines():
    """后端退出钩子：优雅关闭全部账号引擎（ctx.close() 落盘登录态，绝不用 pkill -9）。"""
    try:
        from src import browser_client
        await asyncio.wait_for(browser_client.stop(), timeout=20)
    except Exception as e:  # noqa: BLE001
        print(f"[xhs-leads][server] 引擎关闭异常（非阻断）: {e}")


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
    """本地图片文件名：feed_id 含 #（XHS 原特征），# 在 URL 里是 fragment 会被截断 → 替换为 _。

    R-6.53x：feed_id 空值兜底——入库层已过滤无 feed_id 行，此处再防御防接口 500
    （feed_id None → AttributeError → apiNotes 500 → 前端误判任务结束复位按钮）。
    """
    return f"{(feed_id or '').replace('#', '_')}_{suffix}.jpg"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    _init_tables(con)
    return con


def _add_column_if_missing(con, table: str, col: str, coltype: str) -> None:
    """R-6.37g 拆自 _init_tables：幂等补列（列已存在静默）。"""
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    except sqlite3.OperationalError:
        pass  # 列已存在


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
    _add_column_if_missing(con, "publish_logs", "raw", "TEXT")
    # P1 数据回流：发布记录回写互动数据（浏览/赞/藏/评/转 + 抓取时间）
    _add_column_if_missing(con, "publish_logs", "stats_json", "TEXT")
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
        _add_column_if_missing(con, "clients", col, "TEXT")
    # 评分明细（score_info JSON：4 维度得分，卡片右上角分数可解释）；老库补列
    _add_column_if_missing(con, "notes", "score_info", "TEXT")
    # 视频克隆：详情页原视频地址（extract.js 提取），供一键克隆下载原视频
    _add_column_if_missing(con, "notes", "video_url", "TEXT")
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
_LOGIN_CACHE_TTL = 15.0  # R-6.21d：30s→15s，被踢/失效感知延迟减半（CDP inner_text 开销可控）
# R-6.26 登录失效记忆：登录失效（被踢/风控拦截/页面被平台重定向登录页）是**权威事实**。
# 即使随后采集调试窗被收起、页面信号消失（id_token 残留 → 判定假阳性 logged_in），
# 也必须**持续判失效**直到用户真正重新登录成功（import_cookies 引擎验证通过）才解除——
# 否则 ForceLogin 文案会误显示「当前已登录」、登录引导无法形成重新登录闭环。
_LOGIN_INVALID = False


def _is_login_redirect_url(u: str) -> bool:
    """R-6.37i 拆自 _scan_one_xhs_page：URL 是否登录重定向页。"""
    return "/login" in u or u.rstrip("/").endswith(".xiaohongshu.com/login")


async def _scan_one_xhs_page(p, page):
    """R-6.37g 拆自 _scan_xhs_pages_for_signals：单页处理（首匹配页+正文+登录重定向）。"""
    if p.is_closed() or "xiaohongshu" not in (p.url or ""):
        return page, None, False
    if page is None:
        page = p
    try:
        txt = (await p.inner_text("body")) or ""
    except Exception:  # noqa: BLE001（页面未就绪 → 跳过该页，不误判）
        txt = ""
    is_login = _is_login_redirect_url(p.url or "")
    return page, txt, is_login


async def _scan_xhs_pages_for_signals(ctx):
    """R-6.36e 拆自 _engine_login_state：扫描小红书页（body + 首个匹配页 + 登录重定向信号）。

    R-6.53j：**只扫第一个小红书页**，不再遍历全部——被踢/风控信号为平台**全局渲染**
    （任一打开的小红书画页都会出现对应弹窗/登录墙），单页足够；keeper 每 30s 轮询的
    CDP 压力从「N 页 body」降至「1 页 body」（引擎通常仅 1 个 xhs 页时行为不变）。
    """
    if ctx is None:
        return None, "", False
    bodies: list[str] = []
    page = None
    login_redirect = False
    for p in ctx.pages:
        if p.is_closed() or "xiaohongshu" not in (p.url or ""):
            continue
        page, body_txt, is_login = await _scan_one_xhs_page(p, None)
        if body_txt is not None:
            bodies.append(body_txt)
            login_redirect = login_redirect or is_login
        break  # R-6.53j：单页足够（全局信号），不再遍历全部页面
    return page, "\n".join(bodies), login_redirect


async def _engine_login_state(eng, timeout: float = 15.0) -> tuple[bool, bool, bool, bool]:
    """引擎真实登录态（R 批 v1.5；R-6.6 判定升级；R-6.8 被踢检测统一；
    R-6.22e 风控需人工验证信号；R-6.53i CDP 假死超时防御）：返回
    (web_session 存在?, 页面登录判定?, 被踢信号?, 风控需人工验证?)。

    只读、不清 cookie、不新建浏览器实例（复用引擎现有小红书页，无则开 explore）：
    ① CDP 读引擎内存 cookie → web_session / id_token 是否存在；
    ② 页面注入 extract.js → xhsIsLoggedIn()（仅作辅助判定）；
    ③ **被踢信号检测**：平台判定「设备登录超限/请重新登录」时会在页面渲染登录弹窗，
       即使 id_token/web_session 残留也视为登录态失效（R-6.8：修复「cookie 残留假阳性」）。
    ④ **风控需人工验证检测**：平台风控拦截（「登录后查看（Cookie 有效但被小红书风控拦截，
       需人工扫码/验证后重试）」等）时 cookie 与页面登录标记都在但会话被拦——区别于
       真实有效登录态，前端登录引导须保持打开引导用户扫码（R-6.22e：修复「登录引导
       打开即被假阳性自动关闭」）。

    背景：
    - web_session 存在 ≠ 会话有效（小红书未登录也会种占位 web_session，页面仍弹登录框，
      实测见 R-6，故 R-6.5 引入页面 xhsIsLoggedIn 双判定消除假阳性）。
    - **id_token 是登录成功专属令牌**（未登录会话不种，实测打包态引擎注入后 cookie 含
      id_token len=136 + unread），而引擎页面渲染可能滞后/失败（headless 偶发空白，
      实测注入后 reload 页面空白 → xhsIsLoggedIn 恒 false 造成假阴性，见 R-6.6）。
      因此：id_token 存在 → 直接判已登录（页面判定不阻塞）；否则回退 web_session + 页面判定。
    - **R-6.8 被踢覆盖**：被踢信号只认「登录超限 / 请重新登录」这类明确登录 UI 文本，
      不依赖 xhsIsLoggedIn（空白页无这些文本 → 不误判），任一分支出现该信号 → 判失效。
    - **R-6.53i 假死防御**：CDP 假死（进程存活、WS 未断、协议无响应）时 Playwright 内部
      timeout 依赖 CDP 响应不触发、任务无限挂起（keeper 轮询链被卡死）；外层 wait_for
      超时兜底，超时抛 asyncio.TimeoutError 由调用方处置（login_status → mark_fake_dead
      保持，下次动作 context() 先恢复管理，无法恢复才 force_release 重建）。
    """
    async def _impl() -> tuple[bool, bool, bool, bool]:
        from src import browser_client

        mem = await eng.read_cookies()
        has_ws = any(c.get("name") == "web_session" for c in mem)
        has_id_token = any(c.get("name") == "id_token" for c in mem)
        page_logged_in = False
        kicked = False
        # R-6.8 被踢信号检测（尽力而为）：仅复用**现成**小红书页读取 inner_text；
        # 无页面/读取失败/空白 → 不误判（id_token 权威分支不因页面问题被降级，R-6.6 精神）。
        ctx = None
        try:
            ctx = await eng.context()
        except Exception:  # noqa: BLE001（测试环境/引擎未就绪：无 context 不判被踢）
            ctx = None
        page, body, login_redirect = await _scan_xhs_pages_for_signals(ctx)
        kicked = ("登录超限" in body) or ("请重新登录" in body) or ("登录已过期" in body) or login_redirect
        risk_verify = _detect_risk_verify(body)
        page_logged_in = await _decide_page_logged_in(page, has_id_token, has_ws, kicked)
        return has_ws, page_logged_in, kicked, risk_verify

    return await asyncio.wait_for(_impl(), timeout=timeout)


def _detect_risk_verify(body: str) -> bool:
    """R-6.37a 拆自 _engine_login_state：R-6.22e 风控需人工验证信号（与 risk_manual 审计文案同源）。"""
    return (
        ("登录后查看" in body) or ("风控拦截" in body) or ("需人工扫码" in body)
        or ("人工验证" in body) or ("验证后重试" in body)
    )


async def _decide_page_logged_in(page, has_id_token: bool, has_ws: bool, kicked: bool) -> bool:
    """R-6.37a 拆自 _engine_login_state：页面登录判定（id_token 权威 / ws+页面注入 回退）。"""
    if has_id_token:
        # id_token = 登录成功专属令牌：默认判定通过（页面渲染滞后/空白不阻塞，避免假阴性）；
        # 被踢信号覆盖（平台已踢但 cookie 残留 → 判失效）
        return not kicked
    if has_ws and page is not None:
        # R-6.28：不再 new_page 开 explore 判定页——Tab2 为本地提示页（login-hint）时
        # 每次 login_status 轮询都会新开小红书窗口（用户实测「不必要的窗口闪现/堆叠」）。
        # 无现成小红书页时直接按未登录处理（id_token 权威：登录成功必种 id_token，R-6.6）；
        # 有现成页仍注入判定，被踢/风控信号照常读取（R-6.8/R-6.22e 不降级）。
        try:
            await page.evaluate(injections.get("extract"))
            return bool(await page.evaluate("() => !!xhsIsLoggedIn()")) and not kicked
        except Exception:  # noqa: BLE001（页面未就绪/注入失败 → 不误判已登录）
            return False
    return False


def _login_status_result_dict(has_ws, page_logged_in, kicked, risk_verify, final_logged_in) -> dict:
    """R-6.36g 拆自 _build_login_status_result：组装结果 dict（状态机+raw 诊断文案）。"""
    return {
        "status": "kicked" if kicked else ("logged_in" if final_logged_in else "not_logged_in"),
        "logged_in": final_logged_in,
        "kicked": kicked,
        "risk_verify": risk_verify,
        "nickname": "",
        "raw": f"engine-cdp web_session={'有' if has_ws else '无'} 页面登录={'是' if page_logged_in else '否'} 被踢={'是' if kicked else '否'} 风控需验证={'是' if risk_verify else '否'} 失效记忆={'是' if _LOGIN_INVALID else '否'}",
    }


def _resolve_risk_if_valid(final_logged_in: bool, kicked: bool) -> None:
    """R-6.37i 拆自 _build_login_status_result：登录有效时解除风控（失败静默）。"""
    if final_logged_in and not kicked:
        try:
            risk.resolve("default", reason="登录态有效（login_status 引擎 CDP+页面）")
        except Exception:  # noqa: BLE001
            pass


def _build_login_status_result(has_ws, page_logged_in, kicked, risk_verify) -> dict:
    """R-6.36e 拆自 login_status：判定登录态/失效记忆置位/风控解除，组装结果 dict。"""
    global _LOGIN_INVALID
    logged_in = has_ws and page_logged_in and not kicked
    if kicked or risk_verify:
        _LOGIN_INVALID = True
    final_logged_in = logged_in and not _LOGIN_INVALID
    result = _login_status_result_dict(has_ws, page_logged_in, kicked, risk_verify, final_logged_in)
    _resolve_risk_if_valid(final_logged_in, kicked)
    return result


def _login_status_from_cache(now):
    """命中 15s 缓存则返回结果，否则 None。R-6.26 失效记忆覆盖缓存假阳性。"""
    if _LOGIN_CACHE["data"] is None or now - _LOGIN_CACHE["t"] >= _LOGIN_CACHE_TTL:
        return None
    d = _LOGIN_CACHE["data"]
    if _LOGIN_INVALID and d.get("logged_in"):
        d = dict(d)
        d["logged_in"] = False
        d["status"] = "not_logged_in"
    return d


def _login_status_unknown(raw):
    """引擎侧判定失败/假死时的统一返回体（不对外抛 500，前端据此提示）。"""
    return {"status": "unknown", "logged_in": False, "nickname": "", "raw": raw}


async def _login_status_store_cache(now, result, kicked, risk_verify, eng):
    """正常态写 15s 缓存；已登录时按 5min 节流把 Cookie 落回权威源（R-6.53m）。"""
    if kicked or risk_verify:
        return
    _LOGIN_CACHE["t"] = now
    _LOGIN_CACHE["data"] = result
    if result.get("logged_in"):
        await _persist_session_cookies_if_stale(eng)


@app.get("/api/login_status")
async def login_status():
    """登录态检测（唯一权威 = 引擎 CDP 内存 Cookie；15s 缓存 + 失效记忆覆盖）。

    缓存/失败/落盘细节分别见 _login_status_from_cache / _login_status_unknown /
    _login_status_store_cache；本函数只做编排。
    """
    import time
    global _LOGIN_INVALID

    now = time.monotonic()
    cached = _login_status_from_cache(now)
    if cached is not None:
        return cached
    try:
        from src import engine_pool

        eng = await engine_pool.pool.get("default")
        try:
            has_ws, page_logged_in, kicked, risk_verify = await _engine_login_state(eng)
        except asyncio.TimeoutError:
            eng.mark_fake_dead()
            return _login_status_unknown(
                "引擎 CDP 假死，已保持待恢复；下次操作将尝试恢复，无法恢复则强制重建"
            )
        result = _build_login_status_result(has_ws, page_logged_in, kicked, risk_verify)
        await _login_status_store_cache(now, result, kicked, risk_verify, eng)
        return result
    except Exception as e:  # noqa: BLE001
        return _login_status_unknown(f"引擎判定失败: {str(e)[:80]}")


# ============ 采集调试窗登录提示页（R-6.28 / R-6.50）============
# 未登录时 Tab2（采集/调试窗口）不再显示小红书登录墙，改为本提示页：
# 与 ForceLogin 弹框同一视觉语言（品牌红 #ff2442、Phosphor sign-in 图标、
# 圆角 16、卡片式、禁 emoji）。R-6.50：支持已登录态——已登录显示「已登录·开始采集」，
# 点按钮直接进入采集页 explore；未登录保持「去登录」引导。登录成功后右窗由 keeper
# 保持 explore/login-hint（不再导航 APP 首页，避免登录后右窗出现 APP 主界面回归）。
_LOGIN_HINT_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录小红书</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html,body { height:100%; }
  body {
    display:flex; align-items:center; justify-content:center;
    background:#f5f6f8; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .card {
    width:400px; max-width:calc(100% - 48px); background:#fff; border-radius:16px;
    padding:44px 36px 36px; text-align:center; box-shadow:0 8px 28px rgba(30,30,34,.08);
  }
  .icon { color:#ff2442; display:inline-flex; margin-bottom:20px; }
  h1 { font-size:17px; font-weight:600; color:#1d1d1f; margin-bottom:12px; }
  .desc { font-size:13.5px; line-height:1.75; color:#666; margin-bottom:8px; }
  .foot { font-size:12px; color:#9b9b9f; margin-top:20px; }
  .btn {
    display:inline-block; margin-top:16px; padding:9px 26px; border:0; border-radius:8px;
    background:#ff2442; color:#fff; font-size:14px; font-weight:600; cursor:pointer;
    transition:opacity .15s;
  }
  .btn:hover { opacity:.88; }
  .btn:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
  <div class="card">
    <span class="icon">
      <svg width="44" height="44" viewBox="0 0 256 256" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
        <path d="M141.66,133.66l-40,40a8,8,0,0,1-11.32-11.32L116.69,136H24a8,8,0,0,1,0-16h92.69L90.34,93.66a8,8,0,0,1,11.32-11.32l40,40A8,8,0,0,1,141.66,133.66ZM200,32H136a8,8,0,0,0,0,16h56V208H136a8,8,0,0,0,0,16h64a8,8,0,0,0,8-8V40A8,8,0,0,0,200,32Z"/>
      </svg>
    </span>
    <h1 id="h1t">尚未登录小红书</h1>
    <p class="desc" id="desc">登录后才能开始采集。请在左侧 APP 窗口点击「去登录」，<br>扫码或手机号登录均可。</p>
    <button class="btn" id="go-login" type="button">去登录</button>
    <p class="foot" id="foot">登录成功后，本窗口将自动进入采集页</p>
  </div>
<script>
  // R-6.48h：调试窗口可操作——「去登录」直接触发强制登录引导（左侧 APP 窗口
  // 导航到小红书登录页），不再要求用户必须先去左侧窗口点按钮。
  // R-6.50：已登录态感知——已登录时按钮变「开始采集」直进 explore，避免误导。
  (async function () {
    var btn = document.getElementById('go-login');
    var h1 = document.getElementById('h1t');
    var desc = document.getElementById('desc');
    var foot = document.getElementById('foot');
    try {
      var r = await fetch('/api/login_status');
      var j = await r.json();
      if (j && j.logged_in) {
        h1.textContent = '已登录小红书';
        desc.textContent = '登录态有效，可直接开始采集。';
        btn.textContent = '开始采集';
        foot.textContent = '将打开小红书采集页（explore）';
        btn.addEventListener('click', function () {
          location.href = 'https://www.xiaohongshu.com/explore';
        });
        return;
      }
    } catch (e) { /* 接口失败按未登录展示 */ }
    btn.addEventListener('click', async function () {
      var b = btn;
      b.disabled = true;
      b.textContent = '已打开登录窗口，请扫码…';
      try {
        await fetch('/api/auth/login_begin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: true })
        });
      } catch (e) {
        b.disabled = false;
        b.textContent = '去登录';
      }
    });
  })();
</script>
</body>
</html>"""


@app.get("/login-hint")
async def login_hint():
    """采集调试窗未登录提示页（Tab2 占位，登录后由守卫/import_cookies 导航回 explore）。"""
    return Response(content=_LOGIN_HINT_HTML, media_type="text/html; charset=utf-8")


# ================= 真实登录页方案（R-6.5）=================
# 用户拍板：登录交互改为「壳窗口内打开真实小红书登录页」（原生扫码/手机号+获取验证码），
# 登录后 Cookie 注入无头引擎独立 profile（两套会话闭环：WebView 登录 → CDP 注入 → 引擎复用）。
# 链路：前端引导弹窗「前往登录」→ login_begin 置 pending → launcher 守卫导航登录页
#       （R-6.48q：evt=asyncio.Event 事件驱动唤醒，主循环零轮询）→ 守卫轮询
#       get_cookies 出现 web_session → import_cookies 注入引擎 → 真实判定登录态 → 回 APP 前端。
# evt 由 launcher 启动守卫前注入（同进程同 loop）；无壳/测试直跑 evt=None，守卫退化低频轮询。
_LOGIN_GATE: dict = {"pending": False, "force": False, "evt": None}


class LoginBeginReq(BaseModel):
    force: bool = False


class ImportCookiesReq(BaseModel):
    cookies: list[dict]  # WebView SimpleCookie 转 dict：name/value/domain/path/expires/secure/httpOnly/sameSite


@app.post("/api/auth/login_begin")
async def login_begin(req: LoginBeginReq | None = None):
    """前端请求打开真实登录页（窗口导航由 launcher 守卫执行）。

    force=True：强制登录（启动未登录/被踢、采集异常被踢、APP 强制登录页「去登录」）——
    即使已登录也置 pending；守卫导航登录页后隐藏关闭按钮（不可关闭，登录成功才回）。
    force=False（默认）：主动重登（设置页「重新登录」）——与当前登录态无关，
    总是打开可关闭登录页（可关闭回 APP，登录态不变）。
    R-6.13：移除原「已登录短路」（已登录返回 already_logged_in=True）——用户约定
    设置页「重新登录」应总能触发（换账号/刷新会话），不得被当前登录态拦截。
    """
    cache = _LOGIN_CACHE["data"]
    force = bool(req and req.force)
    _LOGIN_GATE["pending"] = True
    _LOGIN_GATE["force"] = force
    evt = _LOGIN_GATE.get("evt")  # R-6.48q：事件驱动唤醒守卫（同进程同 loop，零轮询）
    if evt is not None:
        evt.set()
    # R-6.22g：登录引导触发即收起采集调试窗（右窗关闭），壳窗（登录页所在）保留——
    # 用户约定「触发需要登录时右侧要么关闭要么有提示」；扫码成功后 new_page 按登录态恢复。
    try:
        from src import engine_pool  # noqa: PLC0415

        eng = await engine_pool.pool.get("default")
        n = await eng.collapse_debug_windows()
        if n:
            log.info("登录引导：收起采集调试窗 %s 个", n)
    except Exception:  # noqa: BLE001（收起失败不阻断登录导航）
        pass
    return {"ok": True, "already_logged_in": False, "force": force}


@app.post("/api/auth/collapse_debug_windows")
async def collapse_debug_windows():
    """R-6.22g：ForceLogin 弹出时前端调用——立即收起采集调试窗（关闭右窗），壳窗保留。"""
    try:
        from src import engine_pool  # noqa: PLC0415

        eng = await engine_pool.pool.get("default")
        n = await eng.collapse_debug_windows()
        return {"ok": True, "closed": n}
    except Exception:  # noqa: BLE001
        return {"ok": True, "closed": 0}


@app.get("/api/auth/login_begin_state")
async def login_begin_state():
    """壳 launcher 守卫轮询：是否有待处理的登录请求（pending 由登录成功清除）+ force 标志。"""
    return {"pending": _LOGIN_GATE["pending"], "force": bool(_LOGIN_GATE.get("force"))}


@app.post("/api/auth/login_cancel")
async def login_cancel():
    """取消待处理登录请求（主动重登用户点「✕」回 APP 时由守卫调用；强制登录无取消路径）。"""
    _LOGIN_GATE["pending"] = False
    _LOGIN_GATE["force"] = False
    evt = _LOGIN_GATE.get("evt")  # R-6.48q：清除待处理事件（守卫已 clear 消费，双保险）
    if evt is not None:
        evt.clear()
    return {"ok": True}


def _to_playwright_cookie(c) -> dict | None:
    """R-6.37i 拆自 _filter_xhs_cookies：单 cookie 转 Playwright 格式（非小红书域返回 None）。"""
    dom = str(c.get("domain", ""))
    if "xiaohongshu" not in dom and "xhscdn" not in dom:
        return None
    item = {
        "name": str(c.get("name", "")),
        "value": str(c.get("value", "")),
        "domain": dom,
        "path": str(c.get("path", "/")),
    }
    exp = c.get("expires")
    if exp is not None:
        try:
            item["expires"] = float(exp)
        except (TypeError, ValueError):
            pass  # 解析失败则 session cookie（不持久）
    if c.get("secure"):
        item["secure"] = True
    if c.get("httpOnly"):
        item["httpOnly"] = True
    ss = c.get("sameSite")
    if ss in ("Lax", "Strict", "None"):
        item["sameSite"] = ss
    return item


def _filter_xhs_cookies(raw) -> list:
    """R-6.37a 拆自 import_cookies：过滤小红书/xhscdn 域 Cookie 并转 Playwright 格式。"""
    pw = []
    for c in raw:
        item = _to_playwright_cookie(c)
        if item is not None:
            pw.append(item)
    return pw


async def _refresh_engine_after_inject(eng) -> None:
    """R-6.37a 拆自 import_cookies：注入后刷新引擎小红书页使登录态生效（无则开 explore）。"""
    from src import engine_pool  # noqa: PLC0415
    ctx = await eng.context()
    page = None
    for p in ctx.pages:
        if not p.is_closed() and "xiaohongshu" in (p.url or ""):
            page = p
            break
    if page is None:
        page = await eng.new_page()
        await page.goto("https://www.xiaohongshu.com/explore",
                        wait_until="domcontentloaded", timeout=30000)
        # R-6.22：新开小红书窗口校准为右半屏（等宽并排不重叠）
        _w_w, _w_h, _, _w_l = engine_pool.shell_window_geometry()
        await _shell_window._calibrate_window(page, _w_l, 0, _w_w, _w_h)
    await page.reload(wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)


@app.post("/api/auth/import_cookies")
async def import_cookies(req: ImportCookiesReq):
    """登录页 Cookie 注入引擎（幂等）：过滤小红书域 → Playwright add_cookies → 刷新 → 真实判定。"""
    global _LOGIN_INVALID  # R-6.26 失效记忆解除点（登录成功验证通过时置 False）
    from src import engine_pool  # noqa: PLC0415（与 login_status 同源延迟导入）

    pw = _filter_xhs_cookies(req.cookies)
    if not pw:
        return {"ok": False, "error": "未捕获到小红书域 Cookie"}

    eng = await engine_pool.pool.get("default")
    ok = await eng.inject_cookies(pw)
    if not ok:
        return {"ok": False, "error": "注入引擎失败"}

    try:
        await _refresh_engine_after_inject(eng)
    except Exception as e:  # noqa: BLE001
        log.warning("注入后刷新引擎页面失败（不影响判定）: %s", e)

    has_ws, page_logged_in, kicked = await _judge_after_inject(eng)
    # R-6.8：被踢（登录超限/请重新登录）→ 视为未通过，守卫留在登录页重新登录
    logged_in = has_ws and page_logged_in and not kicked
    _LOGIN_CACHE["t"] = 0.0  # 强制下次 login_status 真实判定（清 30s 缓存）
    if logged_in:
        # R-6.26：真实重新登录成功（引擎验证通过）→ 解除失效记忆，登录引导闭环完成
        _LOGIN_INVALID = False
        _LOGIN_GATE["pending"] = False
        _LOGIN_GATE["force"] = False
        evt = _LOGIN_GATE.get("evt")  # R-6.48q：清除待处理事件（守卫已 clear 消费，双保险）
        if evt is not None:
            evt.clear()
    return _import_cookies_result(has_ws, page_logged_in, kicked, logged_in)


async def _judge_after_inject(eng) -> tuple[bool, bool, bool]:
    """R-6.37a 拆自 import_cookies：注入后真实判定（异常按未通过处理）。"""
    try:
        has_ws, page_logged_in, kicked, _risk_verify = await _engine_login_state(eng)
    except Exception as e:  # noqa: BLE001（引擎页面刷新/判定时序异常：返回未通过，守卫留在登录页重试）
        log.warning("注入后引擎登录判定异常（不中断，按未通过处理）: %s", e)
        has_ws, page_logged_in, kicked = False, False, False
    return has_ws, page_logged_in, kicked


def _import_cookies_result(has_ws: bool, page_logged_in: bool, kicked: bool, logged_in: bool) -> dict:
    """R-6.37a 拆自 import_cookies：组装注入结果 dict（状态+raw 诊断文案）。"""
    return {
        "ok": True,
        "logged_in": logged_in,
        "status": "kicked" if kicked else ("logged_in" if logged_in else "not_logged_in"),
        "raw": f"注入后 engine-cdp web_session={'有' if has_ws else '无'} 页面登录={'是' if page_logged_in else '否'} 被踢={'是' if kicked else '否'}",
    }




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


class MirrorWindowReq(BaseModel):
    on: bool = True
    account_id: str = "default"


@app.post("/api/mirror/window")
async def mirror_window(req: MirrorWindowReq):
    """调试实时窗口开关（R-6.15）：有头小窗 ↔ 无头（引擎重启生效，登录态保留）。

    用户需求：调试视图不再是「截图映射」，而是引擎独立小窗实时可见采集操作过程。
    """
    from src import mirror
    return await mirror.set_window(req.account_id, req.on)


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
    # R-6.47 全局请求间隔（ms）：真实生效于采集补全节奏（set_rhythm）。
    # 安全范围 1000–10000：下限 1s 尊重「勿再调快」拍板，上限 10s 防呆；默认 1000=(1.0,1.8) 与历史一致。
    interval: int = Field(1000, ge=1000, le=10000)
    # 多账户（P1）：使用哪个账号引擎采集；空 = default（向后兼容）
    account_id: str = ""

async def _require_real_login() -> dict:
    """真实登录校验（采集/搜索前置，解决「纸面登录」空跑问题）。

    纸面登录态（本地 cookies.json 副本）≠ 引擎独立 profile 内真实会话有效：
    小红书 Cookie 有效期短（数小时~数天），副本可能「未过期」而真实会话早已失效，
    导致采集打开页面停在登录界面、一条都采不到。

    校验链路（R 批 v1.4 改造版：登录态 100% 只依赖引擎独立 profile，不读系统 Chrome）：
    ① 引擎 CDP（登录发生在引擎独立 profile 内，内存 Cookie 最实时）→ 有有效 web_session → OK；
    ② 无 → 拦截，提示在 app 内登录小窗完成登录（扫码/手机号/微信途径不限）。

    （方案 A/B「读系统/目标 Chrome Cookie 库自动续命」旧链路已随 R-6.34 收敛删除。）
    """
    from src import engine_pool

    # ① 引擎 CDP：独立 profile 内存 Cookie 最实时（R 批唯一登录态来源）
    try:
        eng = await engine_pool.pool.get("default")
        mem = await eng.read_cookies()
        # R-6.7 口径对齐：id_token 为真实登录权威标志（与 login_status 的
        # _engine_login_state 同判定），存在即放行——不依赖页面渲染（headless
        # 偶发空白 → xhsIsLoggedIn 假阴性坑，登录态明明有效却被拦）。
        # R-6.8 被踢覆盖：平台已踢（登录超限/请重新登录弹窗）但 id_token 残留 → 视为未登录，
        # 走拦截引导重新登录（否则采集 403 与登录态判定的口径再次脱节）。
        if any(c.get("name") == "id_token" for c in mem):
            _, _, kicked, risk_verify = await _engine_login_state(eng)
            # R-6.22e：风控拦截（需人工扫码/验证）时 id_token 残留且未被踢——会话在但被
            # 平台风控拦，不得视为「真实登录有效」放行，须引导用户扫码验证。
            if not kicked and not risk_verify:
                return {"ok": True, "code": "ok", "note": "引擎独立 profile 真实登录有效（CDP id_token）"}
            if risk_verify:
                return {
                    "ok": False, "code": "risk_verify",
                    "note": "账号被小红书风控拦截（Cookie 有效但需人工扫码/验证后重试），请在 app 内完成扫码验证",
                }
            return {
                "ok": False, "code": "kicked",
                "note": "登录已被平台踢出（登录超限/风控），请在 app 内重新登录后重试",
            }
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
    # R-6.53x：提取不完整/抢救中断的行无 feed_id 一律不入库——脏行会导致
    # apiNotes 图片名生成 500，前端收尾拉列表失败误判任务结束复位按钮
    # （「没点停止按钮却变开始采集，调试窗口还在采集」的状态错位）。
    if not n.get("feed_id"):
        return False
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


async def _enrich_notes(notes: list, fast: bool = False, account_id: str = "default",
                        on_progress=None, should_stop=None) -> list:
    """详情补全：互动数据（赞/藏/评/转/发布时间/作者 uid）+ 作者粉丝数（按 uid 缓存）。

    搜索卡片只有点赞粗值；详情页 SSR 提供全量互动与作者 uid，作者主页提供粉丝数
    ——三者对齐后评分才不失真（低粉爆文判断的前提）。

    fast=True 快速档：直连浏览器同页连续导航（无拟人 sleep、渲染等待缩短），20 条约
    5 分钟 → 2 分钟；fast=False 拟人串行（默认，安全优先，保持真人浏览节奏）。
    account_id：使用哪个账号引擎补全详情（默认 default）。
    on_progress(n)：每条补全完成后回调（n=已完成条数），供任务进度/状态条实时反馈
    （R-6.15：补全阶段不再「进度卡死」——搜索采满后 collected 持续更新）。
    should_stop()：R-6.15 补全阶段停止钩子——用户点「停止」时补全循环也立即收尾，
    不再把已搜到的笔记全部补完才停（原实现只停搜索阶段，补全阶段点停止无效）。
    """
    cache: dict = {}
    done = 0
    for n in notes:
        if should_stop and should_stop():
            break
        try:
            if fast:
                await _enrich_fast(n, cache, account_id)
            else:
                n = await get_feed_detail(n, account_id)
                n = await get_user_profile(n, cache, account_id)
        except Exception:  # noqa: BLE001 单条失败不影响整体
            continue
        finally:
            done += 1
            if on_progress:
                on_progress(done)
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


async def _recover_collect_window(account_id: str) -> None:
    """R-6.53v 停止后恢复采集窗口：清理残留小红书采集页（搜索/笔记/探索页）。

    复用 browser_client 清理逻辑（登录墙页保留）——确保停止后引擎不再有
    采集页在导航/滚动，CDP 行为完全停止；主应用窗口（8791）语义保持不变。
    """
    from src import browser_client
    try:
        await browser_client.recover_collect_window(account_id)
    except Exception:  # noqa: BLE001 尽力而为
        log.warning("停止后清理采集窗口失败", exc_info=True)


def _finalize_stopped(state: dict, req, account_id: str) -> None:
    """框架级取消（用户点停止）收尾：已处理入库的条数即最终结果。

    R-6.53z：流式流水线下数据逐条「补全→评分→入库」（state['saved'] 权威计数），
    取消时无需再批量抢救——「采集多少、处理多少、进入列表多少」恒等（所见即所得）；
    未处理的候选（含 search 已提取但未评分入库的）按用户口径不进入结果。
    """
    processed = state.get("saved", 0)
    state["status"] = "stopped"
    state["note"] = f"已停止：采集到 {processed} 条" if processed else "已停止（未采到结果）"
    state["phase"] = "done"


def _collect_orphans() -> tuple[list, set]:
    """标停并取消所有运行中的旧采集任务，返回 (被取消的任务, 涉及账号集合)。"""
    orphans, accounts = [], set()
    for _tid, st in list(_COLLECT_TASKS.items()):
        if st.get("status") == "running":
            st["stop_event"] = True
            task = st.get("task")
            if task and not task.done():
                task.cancel()
                orphans.append(task)
            accounts.add(st.get("account_id", "default"))
    return orphans, accounts


async def _await_orphans(orphans: list) -> None:
    """等旧任务真正收尾（最多 8s）；卡在不可中断 await 时不拖住新采集。"""
    if not orphans:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*orphans, return_exceptions=True), timeout=8)
    except Exception:  # noqa: BLE001
        log.warning("旧采集任务 8s 内未收尾，继续启动新任务")


async def _reset_collect_windows(accounts: set) -> None:
    """逐账号复位采集窗（关闭残留搜索/笔记页），与 collect_stop 同一动作。"""
    for acct in accounts:
        await _recover_collect_window(acct)


async def _stop_running_collect_tasks() -> None:
    """R-6.37g 拆自 collect_start：新任务启动时优雅停止所有运行中的旧任务（非启动限制）。

    R-6.53v：task.cancel() 框架级取消（旧任务所有 await 点被中断，立即收尾）。
    R-6.53z4：与 collect_stop 补齐同一条「停止」语义——旧任务真正结束后逐账号
    _recover_collect_window，否则「被新任务顶掉」这条路径会漏掉采集窗复位。先等
    旧任务收尾再复位，避免复位误关掉新任务刚打开的那一页。
    """
    orphans, accounts = _collect_orphans()
    if not accounts:
        return
    await _await_orphans(orphans)
    await _reset_collect_windows(accounts)


def _collect_finalize_empty(state: dict, res: dict) -> None:
    """search 阶段无候选的收尾：用户停止 vs 平台没加载出内容（0 条=功能未完成，用户口径）。"""
    if state["stop_event"] or res.get("stopped"):
        state["status"] = "stopped"
        state["note"] = "已停止（未采到结果）"
    else:
        # R-6.53u：0 条=功能未完成（用户口径）。明确提示失败而非模糊的「平台仅加载 0 条」
        # ——连续搜索限流/搜索页空壳时出现，用户据此稍后重试。
        state["status"] = "done"
        state["note"] = "未采集到数据（小红书限流或页面未加载出内容），请稍后重试"
    state["phase"] = "done"


def _collect_finalize(state: dict, req, res: dict) -> None:
    """流水线按序入库后的统一收尾：停止 / 采满 / 不足 / 候选全被拒收 四分支。

    「采集多少条、评分多少条、进入列表多少条」恒等（用户口径：所见即所得）。
    四个分支都必须落 status——漏设会让任务永远停在 running，而 running 是
    mirror 切换、keeper tab2 治理、Tab2 关闭三处的唯一判据（状态泄漏会连锁污染壳层）。
    """
    if state["stop_event"] or res.get("stopped"):
        state["status"] = "stopped"
        state["note"] = (
            f"已停止：采集到 {state['saved']} 条" if state["saved"] else "已停止（未采到结果）"
        )
    elif state["saved"] >= req.limit:
        state["status"] = "done"
        state["note"] = f"采集完成：{state['saved']} 条已按评分排序"
    elif state["saved"] > 0:
        state["status"] = "done"
        state["note"] = f"采集完成：平台仅加载 {state['saved']} 条（不足 {req.limit}），已按评分排序"
    else:
        # 候选非空但全部被 _rank_and_insert 拒绝（无 feed_id 的脏行，R-6.53x 起不入库）
        state["status"] = "done"
        state["note"] = "未采集到有效数据（候选均缺少笔记 ID），请稍后重试"
    state["phase"] = "done"


async def _collect_one(item: dict, state: dict, ctx: dict) -> None:
    """单条流水：补全详情 → 本地评分 → 入库 → 播报进度（一条一提交，停止即可见）。"""
    try:
        if ctx["req"].fast:
            await _enrich_fast(item, ctx["cache"], ctx["account_id"])
        else:
            item = await get_feed_detail(item, ctx["account_id"])
            item = await get_user_profile(item, ctx["cache"], ctx["account_id"])
    except Exception:  # noqa: BLE001 单条补全失败不阻断，保留搜索页粗数据评分入库
        pass
    if _rank_and_insert(ctx["con"], item, ctx["req"].keyword, ctx["now"], ctx["account_id"]):
        ctx["con"].commit()  # 逐条提交：停止/取消时已入库的立即可见（所见即所得）
        state["saved"] += 1
        state["collected"] = state["saved"]
        state["partial_items"].append(item)
        state["note"] = f"已采集 {state['saved']} 条"


async def _collect_worker(state: dict, req, account_id: str) -> None:
    """采集 worker：search 提取 → 逐条「补全→评分→入库」→ 统一收尾。

    R-6.53z 流式流水线：不再「先全部提取、再批量补全」（那种批次模型会让界面停在
    补全阶段、停止后结果与显示矛盾）。采满 limit 自动完成，停止时结果=已入库条数。
    R-6.53z1 从 collect_start 内嵌闭包抽为模块级函数：原 CC=19 超「≤8 逐函数硬门槛」，
    且只有经 HTTP 才能间接测到；抽出后可单测直调、状态机逻辑集中在此。
    """
    con = None
    try:
        set_rhythm(req.interval)  # R-6.47：请求间隔真实生效（会话级节奏，默认 1000ms=1–1.8s）
        state["phase"] = "search"
        res = await search_via_browser(
            req.keyword, req.limit,
            # R-6.53v：回调携带 items 引用——任务级取消（collect_stop→task.cancel）时
            # search/enrich 未正常返回，已采数据从该引用抢救，避免取消即丢失。
            on_progress=lambda n, it=None: (
                state.__setitem__("collected", n),
                state.__setitem__("partial_items", it if it is not None else state.get("partial_items", [])),
            ),
            should_stop=lambda: state["stop_event"],
            filters={
                "sort": req.sort, "note_type": req.note_type,
                "time_range": req.time_range, "scope": req.scope, "distance": req.distance,
            },
            account_id=account_id,
        )
        candidates = res["items"]
        if not candidates:
            _collect_finalize_empty(state, res)
            return
        state["phase"] = "enrich"
        state["collected"] = 0
        state["saved"] = 0
        state["partial_items"] = []
        con = _db()  # collect_start 已 _reset_collection_data 清空旧批次，此处只追加
        ctx = {
            "req": req, "account_id": account_id, "con": con, "cache": {},
            "now": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        for item in candidates:
            if state["stop_event"]:
                break  # 停止：立即中断；已处理入库的保留，未处理的丢弃（所见即所得）
            await _collect_one(item, state, ctx)
        con.commit()
        state["phase"] = "save"
        _collect_finalize(state, req, res)
    except asyncio.CancelledError:
        # R-6.53v 框架级停止：collect_stop → task.cancel() 中断所有进行中 await（含 CDP 调用），
        # 不再逐点插 should_stop 检查。此处立即收尾已入库数据（逐条 commit 已保证不丢）。
        _finalize_stopped(state, req, account_id)
        raise
    except Exception as e:  # noqa: BLE001 任务不因单点异常悬挂在 running
        state["status"] = "error"
        state["note"] = f"采集失败：{str(e)[:100]}"
        state["phase"] = "done"
    finally:
        if con is not None:
            con.close()  # _db() 每次新建连接，结束即释放（原闭包版本未关闭）
        _mark_collect_done(account_id)  # 结束即记录，供节流闸使用


@app.post("/api/collect_start")
async def collect_start(req: SearchReq):
    """启动采集任务：后台滚动平台搜索页逐条提取→逐条补全评分入库，直到满额/平台不增长/用户停止。

    account_id 指定使用哪个账号引擎（空 = default）；多账户下采集号/发布号分离。
    """
    account_id = (req.account_id or "default").strip() or "default"
    _check_license_or_raise("collect")  # 授权薄层：collect 功能闸门（free 恒放行）
    await _require_login(account_id)
    # R-6.25 用户对齐：新采集开始时清空旧采集存储（笔记+客户+采集图片），
    # 保证库中只有本次采集数据；登录校验通过后才清，登录失败不破坏数据。
    _reset_collection_data()
    # R-6.22h 用户拍板「不要限制用户」：移除同关键词 60s 防重启动限制（原 dedup 在 R-6.15
    # 被保留为「业务去重」，用户实操反馈同关键词刚结束立即重启被 409 拦截仍属限制）。
    # 同词/换词均允许立即重启；dedup_key 仅留作任务审计展示。
    await _stop_running_collect_tasks()
    tid = uuid.uuid4().hex[:12]
    state = {
        "status": "running", "keyword": req.keyword, "limit": req.limit,
        "collected": 0, "saved": 0, "note": "", "stop_event": False,
        "account_id": account_id, "dedup_key": f"{req.keyword.strip()}|{account_id}",
        "started_ts": time.time(),
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "phase": "search",
    }
    _COLLECT_TASKS[tid] = state
    state["task"] = asyncio.create_task(_collect_worker(state, req, account_id))
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
        # R-6.53z 流式流水线：采集=逐条提取+补全+评分+入库（phase=search/enrich 均在采集中），
        # 动作统一「正在采集」，进度由 note（已采集 N 条）实时播报。
        action = "正在采集"
        return {
            "action": action,
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
    if not st or st.get("status") != "running":
        return {"ok": False, "detail": "没有进行中的采集",
                "account_id": (st or {}).get("account_id", "default")}
    st["stop_event"] = True  # 轮询防线（search/_enrich 既有检查点兜底，双保险）
    task = st.get("task")
    if task and not task.done():
        # R-6.53v 框架级停止：取消整个采集任务——中断所有进行中 await
        # （CDP 导航/提取/滚动/补全等），worker 捕获 CancelledError 收尾已采数据。
        # 相比逐点插 should_stop 检查（旧式散点补丁），任务级取消一处生效、无遗漏窗口。
        task.cancel()
    # 采集窗口 CDP 行为恢复由独立任务执行（不依赖被取消的 worker）：关闭残留
    # 搜索/笔记/探索页，停止后引擎不再有采集页在导航/滚动。
    account_id = st.get("account_id", "default")
    asyncio.create_task(_recover_collect_window(account_id))
    return {"ok": True, "note": "停止已请求，正在收尾…", "account_id": account_id}
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


class OpenFolderReq(BaseModel):
    path: str


def _download_remote_cover(con, zf, fid: str) -> bool:
    """R-6.37i 拆自 _pack_feed_images：远程 cover_url 补下载入包，成功返回 True。"""
    row = con.execute("SELECT cover_url FROM notes WHERE feed_id = ?", (fid,)).fetchone()
    url = (row["cover_url"] if row else "") or ""
    if not url:
        return False
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
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _pack_feed_images(zf, con, fid: str, missing: list) -> None:
    """R-6.37a 拆自 download_images：单篇笔记入包（本地缓存优先，缺失回退远程 cover_url）。"""
    prefix = fid.replace("#", "_")
    files = sorted(IMAGES_DIR.glob(f"{prefix}_*.jpg")) if IMAGES_DIR.exists() else []
    if files:
        for f in files:
            zf.write(f, arcname=f.name)
        return
    if _download_remote_cover(con, zf, fid):
        return
    missing.append(fid)


@app.post("/api/download_images")
def download_images(req: DownloadImgsReq):
    """打包选中笔记的本地图片为 zip，落盘到固定目录并返回路径。

    R-6.23：改为后端直接写文件（不再走浏览器 blob 下载）——内置 CFT 测试浏览器的
    下载 UI 不可用（app 模式无下载栏，「在 Finder 中显示」打不开），用户无法定位
    文件。落盘到 ~/Downloads（用户直觉位置；目录缺失时回退应用数据目录 downloads/），
    前端据此提示路径 + 调 /api/open_folder 在 Finder 中定位。

    优先打包本地缓存（克隆时下载的 fid_pN.jpg / fid_cover.jpg 全部命中）；
    本地无缓存时回退远程 cover_url 补下载（带 Referer，低频，仅用户主动点打包时触发）；
    仍缺失的 feed_id 在返回体 missing 中列出（前端据此提示）。
    """
    if not req.feed_ids:
        raise HTTPException(400, "没有选中的笔记")
    missing: list[str] = []
    con = _db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in req.feed_ids:
            _pack_feed_images(zf, con, fid, missing)
    data = buf.getvalue()
    if not data:
        raise HTTPException(400, "所选笔记没有可打包的图片")
    fname = f"xhs_images_{int(time.time() * 1000)}.zip"
    # R-6.23：落盘到 ~/Downloads（用户直觉位置）；目录不存在/不可写时回退数据目录
    try:
        dl_dir = Path.home() / "Downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        dest = dl_dir / fname
        dest.write_bytes(data)
    except Exception:  # noqa: BLE001（无下载目录/权限异常 → 数据目录兜底）
        (DATA_DIR / "downloads").mkdir(parents=True, exist_ok=True)
        dest = DATA_DIR / "downloads" / fname
        dest.write_bytes(data)
    return {"ok": True, "path": str(dest), "name": fname, "size": len(data), "missing": missing}


@app.post("/api/open_folder")
def open_folder(req: OpenFolderReq):
    """R-6.23：在 Finder 中定位文件（macOS `open -R`），供「下载图片包」查看产物。

    仅允许定位下载目录（~/Downloads）与应用数据目录下的文件，防越权路径。
    """
    p = Path(req.path).expanduser().resolve()
    allowed = [
        (Path.home() / "Downloads").resolve(),
        (DATA_DIR / "downloads").resolve(),
        DATA_DIR.resolve(),
    ]
    if not any(str(p).startswith(str(base)) for base in allowed):
        raise HTTPException(400, "不允许打开该路径")
    if not p.exists():
        raise HTTPException(404, "文件不存在，可能已被移动或删除")
    try:
        subprocess.Popen(["open", "-R", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "path": str(p)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"打开文件夹失败：{e}") from e


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
def _build_week_timeline(day_counts: dict) -> list:
    """R-6.37a 拆自 clients_agg：近 7 天时间线（含今天往前 7 天，无数据的天补 0）。"""
    days = []
    today = datetime.date.today()
    for i in range(6, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        days.append({"date": d, "count": day_counts.get(d, 0)})
    return days


def _tally_row_aux(r, ip_counts, day_counts) -> None:
    """R-6.37i：单行属地/日累计。"""
    ip = (r["ip_location"] or "").strip()
    if ip:
        ip_counts[ip] = ip_counts.get(ip, 0) + 1
    d = (r["created_at"] or "")[:10]
    if d:
        day_counts[d] = day_counts.get(d, 0) + 1


def _tally_client_row(r, dist, ip_counts, day_counts, contact, is_contact) -> int:
    """R-6.37i 拆自 _aggregate_client_rows：单行累计意向/属地/日/联系方式命中。"""
    it = r["intent"] or "low"
    dist[it] = dist.get(it, 0) + 1
    _tally_row_aux(r, ip_counts, day_counts)
    if is_contact(r["comment"] or "", r["matched_word"] or ""):
        contact += 1
    return contact


def _aggregate_client_rows(rows):
    """R-6.37g 拆自 clients_agg：逐行聚合意向分布/属地/日计数/联系方式命中。"""
    from src.intent import is_contact
    dist = {k: 0 for k in ("high", "upper", "middle", "low")}
    ip_counts: dict[str, int] = {}
    day_counts: dict[str, int] = {}
    contact = 0
    for r in rows:
        contact = _tally_client_row(r, dist, ip_counts, day_counts, contact, is_contact)
    return dist, ip_counts, day_counts, contact


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
    dist, ip_counts, day_counts, contact = _aggregate_client_rows(rows)
    intent_dist = [
        {"intent": k, "label": INTENT_LABEL[k], "count": dist[k]}
        for k in sorted(dist, key=intent_rank, reverse=True)
    ]
    ip_top = [{"ip": k, "count": v} for k, v in sorted(ip_counts.items(), key=lambda x: -x[1])[:6]]
    days = _build_week_timeline(day_counts)
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
    """把本地 cookie 列表整理成展示项（按 _COOKIE_ORDER 排序）+ 提取 web_session 摘要。

    登录 UI 改版（用户拍板）：Cookie 只读粒度尽量完整 —— 名称 + 完整值 + 域 + 有效期；
    前端以「隐藏值」开关控制完整值是否脱敏展示（防截图外发泄露会话）。
    """
    items = []
    for i, c in enumerate(cookies):
        nm = c.get("name", "")
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp < 0:
            exp = None
        items.append({
            "name": nm,
            "value": str(c.get("value", "")),  # 完整值（前端默认脱敏，开关可显示原文）
            "domain": str(c.get("domain", ".xiaohongshu.com")),
            "expires": exp if isinstance(exp, (int, float)) else None,  # 会话级 = None
            "ord": i,
        })
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


async def _persist_session_cookies_if_stale(eng, stale_seconds: float = 300.0) -> None:
    """R-6.53m：登录态有效时把引擎内存完整 Cookie（含 web_session）落回 cookies.json。

    仅在文件「距上次落盘超 stale_seconds」且内存含 web_session 时写盘（幂等、节流）。
    失败不阻塞 login_status（落盘是尽力而为的持久化加固，不影响登录判定）。
    """
    try:
        p = DATA_DIR / "cookies.json"
        if p.exists():
            try:
                old = json.loads(p.read_text())
                imported_at = old.get("imported_at", "")
                if imported_at:
                    dt = datetime.datetime.fromisoformat(imported_at)
                    if (datetime.datetime.now() - dt).total_seconds() < stale_seconds:
                        return
            except Exception:  # noqa: BLE001（旧文件解析失败：视为需重写）
                pass
        mem = await eng.read_cookies(timeout=8.0)
        if any(c.get("name") == "web_session" for c in mem):
            _save_cookies_file(mem)
            log.info("R-6.53m 登录态有效，已落盘 cookies.json（含 web_session，%d 条）", len(mem))
    except Exception:  # noqa: BLE001（落盘失败不影响登录判定）
        pass


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


async def _resolve_publish_detail(con, row, req):
    """R-6.37g 拆自 publish_stats：解析笔记详情（用户链接优先，回退来源 feed_id+xsec_token）。"""
    url = (req.note_url or "").strip()
    if url:
        d = await get_note_detail_by_url(url)
        if not d:
            raise HTTPException(400, "打开笔记失败：链接无效或需登录（请确认 Chrome 已登录小红书，且链接是公开笔记）")
        return d
    feed_id = row["feed_id"] or ""
    if not feed_id:
        raise HTTPException(400, "该记录无来源笔记 ID，请粘贴已发布笔记链接后重试")
    # 搜索来源笔记带 xsec_token 才能打开详情页（explore 直开会 404/风控）
    tok = ""
    row2 = con.execute("SELECT xsec_token FROM notes WHERE feed_id=?", (feed_id,)).fetchone()
    if row2 and row2[0]:
        tok = row2[0]
    note = {"feed_id": feed_id, "xsec_token": tok}
    return await get_feed_detail(note)


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
    d = await _resolve_publish_detail(con, row, req)
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


# ====== R-6.18 壳窗口双 Tab：设置项读写（白名单，仅前端设置项） ======
_APP_SETTING_KEYS = ("collect_tab_visible",)  # 采集过程是否在壳窗口 Tab2 显示


class AppConfigReq(BaseModel):
    # R-6.41g：extra='forbid'——未知键在校验层直接 422（pydantic v2 默认静默丢弃）。
    # app_config_set 内 unknown 白名单分支为防御纵深，extra=forbid 后一般不可达，保留不删。
    model_config = ConfigDict(extra="forbid")

    collect_tab_visible: bool | None = None


@app.get("/api/app_config")
def app_config_get():
    """读取 APP 设置（当前仅返回壳窗口 Tab 相关设置项）。"""
    cfg = config.get_config()
    return {k: cfg.get(k) for k in _APP_SETTING_KEYS}


@app.post("/api/app_config")
async def app_config_set(req: AppConfigReq):
    """写入 APP 设置（白名单字段；collect_tab_visible 即时联动 Tab2：开→前置；关→空闲关闭）。"""
    data = req.model_dump(exclude_none=True)
    unknown = set(data) - set(_APP_SETTING_KEYS)
    if unknown:
        raise HTTPException(400, f"不支持设置项: {', '.join(sorted(unknown))}")
    for k, v in data.items():
        config.set_config(k, v)
    if "collect_tab_visible" in data:
        await _sync_tab2_visibility(bool(data["collect_tab_visible"]))
    return {"ok": True, "saved": list(data)}


async def _ensure_tab2_skeleton(eng) -> None:
    """R-6.37a 拆自 _sync_tab2_visibility：无任务 tab 时立即创建 Tab2 骨架（共享 TAB2_GUARD_LOCK）。"""
    from src import engine_pool  # noqa: PLC0415
    async with engine_pool.TAB2_GUARD_LOCK:
        if eng.task_page_snapshot() is not None:
            await eng.task_page_snapshot().bring_to_front()
            return
        page = await eng.new_page()
        try:
            await page.goto(
                "https://www.xiaohongshu.com/explore",
                wait_until="domcontentloaded", timeout=10000,
            )
            from src import shell_window  # noqa: PLC0415

            await shell_window._ensure_debug_title(page)  # 采集调试窗口标题守护
            # R-6.22：新开采集骨架窗口校准为右半屏（等宽并排不重叠）
            _w_w, _w_h, _, _w_l = engine_pool.shell_window_geometry()
            await shell_window._calibrate_window(page, _w_l, 0, _w_w, _w_h)
            await cdp_call(page.bring_to_front(), timeout=CDP_QUICK,
                           label="server.tab2_bring_to_front", skip=True)
            log.info("app_config：已创建 Tab2 采集骨架（开关开）")
        except Exception:  # noqa: BLE001
            log.warning("app_config：Tab2 骨架创建失败（keeper 兜底轮询接管）")


async def _close_idle_tab2_family(eng) -> None:
    """R-6.37a 拆自 _sync_tab2_visibility：空闲时收起所有 Tab2 家族页（explore 骨架/残留采集页）。"""
    ctx = await eng.context()
    closed = 0
    for p in list(ctx.pages):
        if p.is_closed():
            continue
        u = p.url or ""
        if "xiaohongshu.com" in u and "/login" not in u:
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                pass
            closed += 1
    log.info(f"app_config：空闲采集 tab 已收起（开关关，关闭 {closed} 个）")


async def _sync_tab2_visibility(visible: bool) -> None:
    """R-6.19 开关即时联动：开→Tab2 前置/创建骨架；关→空闲采集 tab 关闭（采集中不打断）。"""
    try:
        from src import engine_pool  # noqa: PLC0415

        eng = await engine_pool.pool.get("default")
        tp = eng.task_page_snapshot()
        if visible:
            if tp is None:
                await _ensure_tab2_skeleton(eng)
            else:
                try:
                    await tp.bring_to_front()
                except Exception:  # noqa: BLE001
                    pass
                log.info("app_config：采集 tab 前置（开关开）")
        else:
            running = any(
                st.get("status") == "running" for st in _COLLECT_TASKS.values()
            )
            if running:
                log.info("app_config：采集中，Tab2 保持后台（开关关，任务结束后自然闲置）")
                return
            await _close_idle_tab2_family(eng)
    except Exception as e:  # noqa: BLE001
        log.warning("app_config Tab2 联动失败: %s", e)


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


def _build_ai_system_prompt() -> str:
    """R-6.37i 拆自 ai_generate：组装系统提示（基础人设 + 已配置账号人设注入）。"""
    system_parts = ["你是一个专业的小红书内容创作助手，输出精炼、可直接使用，不要客套。"]
    persona = _load_persona()
    if persona.get("industry") or persona.get("tone") or persona.get("topics"):
        system_parts.append(
            "账号人设：行业【{}】、口吻【{}】、常发主题【{}】。生成内容保持该人设一致，"
            "避免“宝子们/家人们/谁懂啊”式通用AI味。".format(
                persona.get("industry", ""), persona.get("tone", ""), persona.get("topics", "")
            )
        )
    return "\n".join(system_parts)


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
        system_prompt = _build_ai_system_prompt()
        result = await llm_service.chat(system_prompt, prompt)
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
