"""xhs-leads 后端壳：REST API + SQLite 读写 + 静态前端服务（engine_pool 引擎池 CDP 直连，非外部 MCP 封装）。

运行:
  uvicorn server:app --host 127.0.0.1 --port 8791

API:
  GET  /                    → 前端界面（frontend/dist/index.html）
  GET  /api/login_status    → 登录状态
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
from pydantic import BaseModel, Field

# ---- 统一日志（审计账本） ----
from src import logger as _log
from src import save_dialog as _save_dialog  # 原生「另存为」面板（导出统一走它）

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

from src.score import (  # noqa: E402
    rank_notes, score_note_hot, _to_int, fans_for_db,
)
from src.jsonio import read_json_safe, write_json  # noqa: E402
from src.browser_client import BrowserLoginRequiredError
from src.collect_pipeline import (  # noqa: E402
    get_feed_detail,
    get_user_profile,
    search_via_browser,
    set_rhythm,
)
from src import browser_client  # noqa: E402（快速档 _enrich_notes 直连详情/主页）
from src import injections
from src import config, risk  # noqa: E402（架构改造 P1：配置中心 + 每账号风控）
from src import license as lic  # noqa: E402（授权薄层：free 恒放行，业务入口统一过闸）

app = FastAPI(title="XHS Leads · 小红书获客助手")



def _mark_collect_done(account_id: str = "default") -> None:
    """平台任务结束（成功/停止/失败）统一记录结束时间，供节流闸使用。"""
    risk.mark_collect_done(account_id or "default")


def _reset_collection_data() -> int:
    """清空「本批采集」数据：笔记 + 采集条件上下文 + 采集下载的本地图片。

    **客户库不清**（clients 表跨会话累积，见 /api/clients_clear 的显式清空入口）——
    客户是逐条评论沉淀下来的线索资产，重开应用/换关键词重采都不该把它抹掉；
    同一篇笔记重采时按 (uid, feed_id, comment) 去重合并，人数口径按 uid 收敛。

    语义：应用重启时清空一次；前端点「开始采集」前调 /api/collect_clear 清一次。
    本函数只被这两处显式调用，不再由 collect_start 隐式触发。
    user_ 前缀（发布页「追加图片」上传资产）属用户主动操作产物，保留。
    返回删除的图片文件数。
    """
    con = _db()
    con.execute("DELETE FROM notes")
    con.execute("DELETE FROM collect_ctx")
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
    """启动时清理：残留任务 tab + 本批采集数据（客户库保留）。best-effort 不阻塞启动。

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
        log.warning(f"引擎关闭异常（非阻断）: {e}")


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

    feed_id 空值兜底——入库层已过滤无 feed_id 行，此处再防御防接口 500
    （feed_id None → AttributeError → apiNotes 500 → 前端误判任务结束复位按钮）。
    """
    return f"{(feed_id or '').replace('#', '_')}_{suffix}.jpg"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    _init_tables(con)
    return con


def _add_column_if_missing(con, table: str, col: str, coltype: str) -> None:
    """幂等补列（列已存在静默）。"""
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    except sqlite3.OperationalError:
        pass  # 列已存在


def _migrate_clients_pk(con) -> None:
    """老库 clients 主键是 (uid, feed_id)，一人多条评论只存 1 条会被压掉。

    改成 (uid, feed_id, comment)：同一人的不同评论都保留（作者本人的评论在入库前
    就排除了，不靠主键过滤）。迁移保留原有数据；同一人只有一条评论时结果不变。
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='clients'"
    ).fetchone()
    if not row or not row[0] or "PRIMARY KEY (uid, feed_id, comment)" in row[0]:
        return
    con.execute("ALTER TABLE clients RENAME TO clients_old_pk")
    con.execute("""
        CREATE TABLE clients (
            uid TEXT, feed_id TEXT, nickname TEXT, comment TEXT, profile TEXT, created_at TEXT,
            intent TEXT, matched_word TEXT, ip_location TEXT, comment_time TEXT,
            PRIMARY KEY (uid, feed_id, comment)
        )
    """)
    con.execute("INSERT OR IGNORE INTO clients SELECT * FROM clients_old_pk")
    con.execute("DROP TABLE clients_old_pk")
    con.commit()


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
    # P4 采集客户：互动用户名单（评论用户）
    # 主键含 comment：同一个人在不同时间发的多条评论都要留着（「多少钱」→「能便宜吗」
    # 是两条需求信号，压成一条会丢掉后一条）。人数口径在查询时按 uid 去重，存储不压。
    con.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            uid TEXT, feed_id TEXT, nickname TEXT, comment TEXT, profile TEXT, created_at TEXT,
            intent TEXT, matched_word TEXT, ip_location TEXT, comment_time TEXT,
            PRIMARY KEY (uid, feed_id, comment)
        )
    """)
    _migrate_clients_pk(con)
    # O1-3：老库补意向/属地/时间列（幂等）
    for col in ("intent", "matched_word", "ip_location", "comment_time"):
        _add_column_if_missing(con, "clients", col, "TEXT")
    # 评分明细（score_info JSON：4 维度得分，卡片右上角分数可解释）；老库补列
    _add_column_if_missing(con, "notes", "score_info", "TEXT")
    # 第二口径「综合热度」分数与明细：与低粉分同时入库，榜单切换时零重算
    _add_column_if_missing(con, "notes", "hot_score", "REAL")
    _add_column_if_missing(con, "notes", "hot_info", "TEXT")
    # 视频克隆：详情页原视频地址（extract.js 提取），供一键克隆下载原视频
    _add_column_if_missing(con, "notes", "video_url", "TEXT")
    # 实况图（动图，页面 LIVE 角标）：类型仍是图文，单独记张数与原片地址（JSON 数组）
    _add_column_if_missing(con, "notes", "live_photo", "INTEGER")
    _add_column_if_missing(con, "notes", "live_videos", "TEXT")
    # 采集时拿到的详情图（JSON 数组）：一键克隆优先用这份，避免克隆时再去开详情页
    _add_column_if_missing(con, "notes", "detail_imgs", "TEXT")
    # 最近一次采集的筛选条件（按关键词）：供「采集客户」无活动页时按同样条件重开搜索页，
    # 否则重开得到综合排序的另一批结果，原卡片多半不在里面。
    con.execute("""
        CREATE TABLE IF NOT EXISTS collect_ctx (
            keyword TEXT PRIMARY KEY, params TEXT, ts TEXT
        )
    """)
    # 架构改造 P1：多账户维度 —— 各表补 account_id（默认 default，老数据兼容）
    for _tbl in ("notes", "clients"):
        try:
            con.execute(f"ALTER TABLE {_tbl} ADD COLUMN account_id TEXT DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # 列已存在
    # 原作品链接（打开原作品跳转）：ALTER 补到末尾，保证新老库列序一致（INSERT 用全列 VALUES）
    _add_column_if_missing(con, "notes", "origin", "TEXT")
    _backfill_hot_scores(con)
    con.commit()


def _backfill_hot_scores(con) -> int:
    """给入库时还没有第二口径的行补「综合热度」分（幂等，无缺失行时一次 SELECT 即返回）。

    新数据在 _rank_and_insert 里两套分一起写；只有加列之前的历史行需要补，
    否则按热度排序时这些行 hot_score 为 NULL 会全被排到最后。
    """
    rows = con.execute("SELECT * FROM notes WHERE hot_score IS NULL").fetchall()
    if not rows:
        return 0
    for r in rows:
        d = dict(r)
        si = score_note_hot(d)
        con.execute("UPDATE notes SET hot_score=?, hot_info=? WHERE feed_id=?",
                    (si["score"], json.dumps(si, ensure_ascii=False), d["feed_id"]))
    con.commit()
    return len(rows)


# ================= 登录 =================
# 登录态缓存：Chrome CDP 连接会弹「远程调试授权」窗（每次连接必弹，官方无法关闭）。
# 前端轮询 login_status 若每次都连 Chrome → 每几秒弹一次窗（用户痛点「关了又开」）。
# 因此缓存检测结果 TTL=30s，轮询打缓存，不反复连 Chrome。
_LOGIN_CACHE = {"t": 0.0, "data": None}
_LOGIN_CACHE_TTL = 15.0
# 即使随后采集调试窗被收起、页面信号消失（id_token 残留 → 判定假阳性 logged_in），
# 也必须**持续判失效**直到用户真正重新登录成功（login_ok 通知 + 引擎实况复核）才解除——
# 否则 ForceLogin 文案会误显示「当前已登录」、登录引导无法形成重新登录闭环。
_LOGIN_INVALID = False


def _is_login_redirect_url(u: str) -> bool:
    """URL 是否登录重定向页。"""
    return "/login" in u or u.rstrip("/").endswith(".xiaohongshu.com/login")


async def _scan_one_xhs_page(p, page):
    """单页处理（首匹配页+正文+登录重定向）。"""
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
    """扫描小红书页（body + 首个匹配页 + 登录重定向信号）。

    **只扫第一个小红书页**，不再遍历全部——被踢/风控信号为平台**全局渲染**
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
        break
    return page, "\n".join(bodies), login_redirect


async def _engine_login_state(eng, timeout: float = 15.0) -> tuple[bool, bool, bool, bool]:
    """引擎真实登录态（；判定升级；被踢检测统一；
    风控需人工验证信号；CDP 假死超时防御）：返回
    (web_session 存在?, 页面登录判定?, 被踢信号?, 风控需人工验证?)。

    只读、不清 cookie、不新建浏览器实例（复用引擎现有小红书页，无则开 explore）：
    ① CDP 读引擎内存 cookie → web_session / id_token 是否存在；
    ② 页面注入 extract.js → xhsIsLoggedIn()（仅作辅助判定）；
    ③ **被踢信号检测**：平台判定「设备登录超限/请重新登录」时会在页面渲染登录弹窗，
       即使 id_token/web_session 残留也视为登录态失效。
    ④ **风控需人工验证检测**：平台风控拦截（「登录后查看（Cookie 有效但被小红书风控拦截，
       需人工扫码/验证后重试）」等）时 cookie 与页面登录标记都在但会话被拦——区别于
       真实有效登录态，前端登录引导须保持打开引导用户扫码（修复「登录引导
       打开即被假阳性自动关闭」）。

    背景：
    - web_session 存在 ≠ 会话有效（小红书未登录也会种占位 web_session，页面仍弹登录框，
      实测见，故引入页面 xhsIsLoggedIn 双判定消除假阳性）。
    - **id_token 是登录成功专属令牌**（未登录会话不种，实测打包态引擎注入后 cookie 含
      id_token len=136 + unread），而引擎页面渲染可能滞后/失败（headless 偶发空白，
      实测注入后 reload 页面空白 → xhsIsLoggedIn 恒 false 造成假阴性，见）。
      因此：id_token 存在 → 直接判已登录（页面判定不阻塞）；否则回退 web_session + 页面判定。
    - **被踢覆盖**：被踢信号只认「登录超限 / 请重新登录」这类明确登录 UI 文本，
      不依赖 xhsIsLoggedIn（空白页无这些文本 → 不误判），任一分支出现该信号 → 判失效。
    - **假死防御**：CDP 假死（进程存活、WS 未断、协议无响应）时 Playwright 内部
      timeout 依赖 CDP 响应不触发、任务无限挂起（keeper 轮询链被卡死）；外层 wait_for
      超时兜底，超时抛 asyncio.TimeoutError 由调用方处置（login_status → mark_fake_dead
      保持，下次动作 context() 先恢复管理，无法恢复才 force_release 重建）。
    """
    async def _impl() -> tuple[bool, bool, bool, bool]:

        mem = await eng.read_cookies()
        has_ws = any(c.get("name") == "web_session" for c in mem)
        has_id_token = any(c.get("name") == "id_token" for c in mem)
        page_logged_in = False
        kicked = False
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
    """风控需人工验证信号（与 risk_manual 审计文案同源）。"""
    return (
        ("登录后查看" in body) or ("风控拦截" in body) or ("需人工扫码" in body)
        or ("人工验证" in body) or ("验证后重试" in body)
    )


async def _page_is_guest_render(page) -> bool:
    """页面是否**明确**处于游客渲染态（手机号框 / 「登录探索更多内容」）。

    与 browser_client 同一判据（NEEDS_LOGIN_JS，唯一事实源），
    消除「后端说已登录、采集侧说游客态」的口径分裂。读不到页面 → 返回 False
    （不作否决，保留 id_token 兜底语义，避免空白页假阴性，）。
    """
    try:
        from src.browser_client import NEEDS_LOGIN_JS  # noqa: PLC0415

        return bool(await page.evaluate(NEEDS_LOGIN_JS))
    except Exception:  # noqa: BLE001（页面未就绪/注入失败：不否决）
        return False


async def _decide_page_logged_in(page, has_id_token: bool, has_ws: bool, kicked: bool) -> bool:
    """页面登录判定（id_token 权威 / ws+页面注入 回退）。

    **页面明确游客态优先否决**——实测 web_session/id_token 残留而页面整页
    游客渲染（点搜索框只弹登录浮层），此时任何 cookie 结论都是假阳性。页面不可读时
    才退回 id_token 兜底。
    """
    if kicked:
        return False
    if page is not None:
        # 停在 /login 本身就是"这个会话要重新登录"的明确信号（已登录时平台会自己跳
        # /explore）。cookie 残留不得覆盖它，否则前端以为已登录、不弹登录引导。
        if "/login" in str(getattr(page, "url", "") or ""):
            return False
        if await _page_is_guest_render(page):
            return False
    return await _decide_by_tokens(page, has_id_token, has_ws)


async def _decide_by_tokens(page, has_id_token: bool, has_ws: bool) -> bool:
    """按令牌/页面注入判定（游客态与被踢已在外层处理）。"""
    if has_id_token:
        # id_token = 登录成功专属令牌：默认判定通过（页面渲染滞后/空白不阻塞，避免假阴性）
        return True
    if not (has_ws and page is not None):
        return False
    # 每次 login_status 轮询都会新开小红书窗口（用户实测「不必要的窗口闪现/堆叠」）。
    try:
        await page.evaluate(injections.get("extract"))
        return bool(await page.evaluate("() => !!xhsIsLoggedIn()"))
    except Exception:  # noqa: BLE001（页面未就绪/注入失败 → 不误判已登录）
        return False


def _login_status_result_dict(has_ws, page_logged_in, kicked, risk_verify, final_logged_in) -> dict:
    """组装结果 dict（状态机+raw 诊断文案）。"""
    return {
        "status": "kicked" if kicked else ("logged_in" if final_logged_in else "not_logged_in"),
        "logged_in": final_logged_in,
        "kicked": kicked,
        "risk_verify": risk_verify,
        "nickname": "",
        "raw": f"engine-cdp web_session={'有' if has_ws else '无'} 页面登录={'是' if page_logged_in else '否'} 被踢={'是' if kicked else '否'} 风控需验证={'是' if risk_verify else '否'} 失效记忆={'是' if _LOGIN_INVALID else '否'}",
    }


def _resolve_risk_if_valid(final_logged_in: bool, kicked: bool) -> None:
    """登录有效时解除风控（失败静默）。"""
    if final_logged_in and not kicked:
        try:
            risk.resolve("default", reason="登录态有效（login_status 引擎 CDP+页面）")
        except Exception:  # noqa: BLE001
            pass


def _build_login_status_result(has_ws, page_logged_in, kicked, risk_verify) -> dict:
    """判定登录态/失效记忆置位/风控解除，组装结果 dict。"""
    global _LOGIN_INVALID
    logged_in = has_ws and page_logged_in and not kicked
    if kicked or risk_verify:
        _LOGIN_INVALID = True
    elif logged_in:
        # 失效记忆。旧实现只在注入链路成功时复位，于是「用户已在引擎窗口
        # 扫码成功、引擎 Cookie 与页面都有效」仍被判未登录，只能重启 app 才恢复——
        # 表现为「扫了码左边还在等、点开始采集一直被拦」。
        _LOGIN_INVALID = False
    final_logged_in = logged_in and not _LOGIN_INVALID
    result = _login_status_result_dict(has_ws, page_logged_in, kicked, risk_verify, final_logged_in)
    _resolve_risk_if_valid(final_logged_in, kicked)
    return result


def _login_status_from_cache(now):
    """命中 15s 缓存则返回结果，否则 None。失效记忆覆盖缓存假阳性。"""
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


def _login_status_store_cache(now, result, kicked, risk_verify):
    """正常态写 15s 缓存。

    不再把引擎内存 Cookie 落回 cookies.json——profile 本身就是权威源，
    浏览器会把 Cookie 写在账号目录里；落盘只是一份会过期的旧快照。
    """
    if kicked or risk_verify:
        return
    _LOGIN_CACHE["t"] = now
    _LOGIN_CACHE["data"] = result


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
        # 等用户扫码，不是被平台踢出。对外状态统一成 not_logged_in，避免前端把
        # 「还没扫码」显示成「账号被踢」，让用户误以为号废了。
        if kicked and not risk_verify and await _engine_on_login_page(eng):
            result = dict(result, status="not_logged_in", kicked=False)
            result["raw"] = str(result.get("raw", "")) + "（页面停在登录页→按未登录）"
        _login_status_store_cache(now, result, kicked, risk_verify)
        return result
    except Exception as e:  # noqa: BLE001
        return _login_status_unknown(f"引擎判定失败: {str(e)[:80]}")


# 未登录时 采集窗（采集/调试窗口）不再显示小红书登录墙，改为本提示页：
# 与 ForceLogin 弹框同一视觉语言（品牌红 #ff2442、Phosphor sign-in 图标、
# 点按钮直接进入采集页 explore；未登录保持「去登录」引导。登录成功后右窗由 keeper
# 保持 explore/login-hint（不再导航 APP 首页，避免登录后右窗出现 APP 主界面回归）。



# 用户拍板：登录交互改为「壳窗口内打开真实小红书登录页」（原生扫码/手机号+获取验证码），
# 壳窗/采集窗/引擎共用同一个 CFT 实例与 profile，登录态天然共享。
# 链路：前端引导弹窗「前往登录」→ login_begin 置 pending → launcher 守卫导航登录页
#       get_cookies 出现 web_session → POST login_ok（仅通知）→ 真实判定登录态 → 回 APP 前端。
# evt 由 launcher 启动守卫前注入（同进程同 loop）；无壳/测试直跑 evt=None，守卫退化低频轮询。
_LOGIN_GATE: dict = {"pending": False, "force": False, "evt": None}


class LoginBeginReq(BaseModel):
    force: bool = False


@app.post("/api/auth/login_begin")
async def login_begin(req: LoginBeginReq | None = None):
    """前端请求打开真实登录页（窗口导航由 launcher 守卫执行）。

    force=True：强制登录（启动未登录/被踢、采集异常被踢、APP 强制登录页「去登录」）——
    即使已登录也置 pending；守卫导航登录页后隐藏关闭按钮（不可关闭，登录成功才回）。
    force=False（默认）：主动重登（设置页「重新登录」）——与当前登录态无关，
    总是打开可关闭登录页（可关闭回 APP，登录态不变）。
    移除原「已登录短路」（已登录返回 already_logged_in=True）——用户约定
    设置页「重新登录」应总能触发（换账号/刷新会话），不得被当前登录态拦截。
    """
    force = bool(req and req.force)
    _LOGIN_GATE["pending"] = True
    _LOGIN_GATE["force"] = force
    evt = _LOGIN_GATE.get("evt")
    if evt is not None:
        evt.set()
    # 右窗现在兼任登录页容器，收起它等于把登录入口关掉；守卫导航前会按需新开。
    return {"ok": True, "already_logged_in": False, "force": force}


@app.post("/api/auth/login_cancel")
async def login_cancel():
    """取消待处理登录请求（主动重登用户点「✕」回 APP 时由守卫调用；强制登录无取消路径）。"""
    _LOGIN_GATE["pending"] = False
    _LOGIN_GATE["force"] = False
    evt = _LOGIN_GATE.get("evt")
    if evt is not None:
        evt.clear()
    return {"ok": True}


def _mark_login_ok() -> None:
    """登录成功后的统一收尾：解失效记忆、清登录门、推事件。

    **不唤醒、不续采**——采集早已改成「撞墙即收尾」，没有挂起等待的任务
    可唤醒；这里只是把登录门复位并通知前端登录态已恢复。
    """
    global _LOGIN_INVALID
    _emit_event("login", {"logged_in": True})  # 登录态变化推一次，前端不再定时查
    _LOGIN_INVALID = False
    _LOGIN_GATE["pending"] = False
    _LOGIN_GATE["force"] = False
    evt = _LOGIN_GATE.get("evt")
    if evt is not None:
        evt.clear()


@app.post("/api/auth/login_ok")
async def login_ok():
    """登录成功通知（守卫在登录页检测到登录 Cookie 后调用）。

    守卫只凭「浏览器里有 id_token / web_session」就通知，而残留的 web_session
    可能已被平台判失效（踢出）或被风控拦截——实测出现过「页面停在 /login、
    cookie 还在，却被当成登录成功」的假阳性。故这里不再无条件放行，而是以
    **引擎实况复核为唯一权威**：真的已登录、未被踢、未被风控拦截，才收尾
    （解除失效记忆、清登录门、推事件）并返回 logged_in=True；否则返回
    logged_in=False，守卫据此继续监视，不解除失效记忆。

    这里不搬运 Cookie（壳窗/采集窗/引擎是同一个 CFT 实例、同一个 profile，
    Cookie 由浏览器自己写盘）。引擎未就绪 / CDP 假死时保守返回 false，
    绝不判成功。
    """
    _LOGIN_CACHE["t"] = 0.0  # 强制下次 login_status 真实判定（清缓存）
    try:
        from src import engine_pool

        eng = await engine_pool.pool.get("default")
        has_ws, page_logged_in, kicked, risk_verify = await _engine_login_state(eng)
    except Exception:  # noqa: BLE001（引擎未就绪/CDP 假死：保守不判成功）
        return {"ok": True, "logged_in": False}
    if not (has_ws and page_logged_in and not kicked and not risk_verify):
        return {"ok": True, "logged_in": False}
    _mark_login_ok()
    return {"ok": True, "logged_in": True}


@app.post("/api/mirror/open")
async def mirror_open(account_id: str = "default"):
    """登录小窗打开：确保引擎打开小红书登录页（截图流/事件转发的目标页就绪）。"""
    from src import engine_window
    return await engine_window.open_login(account_id)


@app.post("/api/mirror/close")
async def mirror_close(account_id: str = "default"):
    """关闭账号引擎：第三类账号开出来必须能收回去。

    default 拒绝（承载 APP 壳窗与采集窗）；有采集任务在跑先拒绝。
    """
    from src import engine_window
    return await engine_window.close_account(account_id)



class SearchReq(BaseModel):
    keyword: str
    # 上限硬约束：1~200 条，防止脚本/手动传入超大值导致无限采集（pydantic 直接 422 拒绝）
    limit: int = Field(20, ge=1, le=200)
    # 搜索筛选（对应小红书「筛选」弹层，中文选项；默认值=不筛选）
    sort: str = ""
    note_type: str = ""
    time_range: str = ""
    scope: str = ""
    distance: str = ""
    # 快速档：同页连续导航补全（去拟人等待），默认关（拟人串行，安全优先）
    fast: bool = False
    # 安全范围 1000–10000：下限 1s 尊重「勿再调快」拍板，上限 10s 防呆；默认 1000=(1.0,1.8) 与历史一致。
    interval: int = Field(1000, ge=1000, le=10000)
    # 多账户（P1）：使用哪个账号引擎采集；空 = default（向后兼容）
    account_id: str = ""

_REAL_LOGIN_TIMEOUT_S = 3.0


async def _engine_on_login_page(eng) -> bool:
    """引擎现成的小红书页是否停在登录页（只读 URL，不读 body，极轻）。

    唯一用途是**区分文案语义**：页面停在 /login 是「还没登录（等扫码）」，
    不是「被平台踢出」。两者在 `_engine_login_state` 里都表现为 kicked（
    login_redirect 参与判定，防止 cookie 残留假阳性放行），但文案混用会让
    用户误以为账号被封，与「没登录就直接返回未登录」的口径冲突。
    """
    try:
        ctx = await eng.context()
    except Exception:  # noqa: BLE001（引擎未就绪：不判，走原被踢文案）
        return False
    for p in ctx.pages:
        u = p.url or ""
        if p.is_closed() or "xiaohongshu" not in u:
            continue
        return _is_login_redirect_url(u)
    return False


async def _real_login_probe() -> dict:
    """真实登录探针（无超时包装）：一次引擎 Cookie 读取 + 一次登录态判定。

    纸面登录态（外部留存的旧 Cookie 快照）≠ 引擎 profile 内真实会话有效：
    小红书 Cookie 有效期短（数小时~数天），旧快照可能「未过期」而真实会话早已失效，
    导致采集打开页面停在登录界面、一条都采不到。

    R 批 v1.4：登录态 100% 只依赖引擎独立 profile，不读系统 Chrome
    （方案 A/B「读系统/目标 Chrome Cookie 库自动续命」旧链路已随收敛删除）。
    """
    from src import engine_pool

    eng = await engine_pool.pool.get("default")
    mem = await eng.read_cookies()
    # _engine_login_state 同判定），存在即放行——不依赖页面渲染（headless
    # 偶发空白 → xhsIsLoggedIn 假阴性坑，登录态明明有效却被拦）。
    if any(c.get("name") == "id_token" for c in mem):
        # explore 瀑布流页上万节点，一次 >3s——把「点开始采集」本身变成新的墙
        # （实测 collect_start 直接返回「登录校验超时」）。id_token 是登录权威标志，
        # 只再读一个最轻的 URL 信号就够；真被踢/风控由采集撞墙兜底
        # （BrowserLoginRequiredError → 前端提示重新登录）。
        if await _engine_on_login_page(eng):
            # 仍然拦住采集（页面在登录页确实采不到），但文案必须如实。
            return {
                "ok": False, "code": "not_logged_in",
                "note": "尚未登录小红书：右侧登录小窗当前停在登录页，请扫码登录后重试",
            }
        return {"ok": True, "code": "ok", "note": "引擎独立 profile 真实登录有效（id_token）"}
    if any(c.get("name") == "web_session" for c in mem):
        # 有 web_session 且页面不在登录页即视为会话可用；真失效由采集撞墙兜底。
        if not await _engine_on_login_page(eng):
            return {"ok": True, "code": "ok", "note": "引擎独立 profile 真实登录有效（web_session）"}
    return {
        "ok": False, "code": "not_logged_in",
        "note": "独立 profile 无有效小红书登录态：请在 app 内登录小窗扫码/手机号/微信登录（任一途径）",
    }


async def _require_real_login() -> dict:
    """真实登录校验（采集/搜索前置）：**一问一答 + 硬超时**，绝不把用户拖进「等待」。

    （用户拍板：守护会造成不可控局面，完全抛弃）：
    超时就超时，给提示让用户重试；不做后台等待、不自动续采、不自动拉回。
    引擎页面卡死（CDP 无响应）时旧实现会让「点开始采集」卡在这里几十秒——
    正是用户看到的「左边一直在等待」。现在整体 3s 上限，超时按未登录返回。
    """
    try:
        return await asyncio.wait_for(_real_login_probe(), timeout=_REAL_LOGIN_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("真实登录校验超时（%.1fs），按未登录返回", _REAL_LOGIN_TIMEOUT_S)
        return {
            "ok": False, "code": "timeout",
            "note": f"登录校验超时（引擎 {_REAL_LOGIN_TIMEOUT_S:.0f}s 内无响应），请稍后重试；"
                    "若反复出现请在 app 内重新登录",
        }
    except Exception as e:  # noqa: BLE001
        log.warning("引擎 CDP 真实校验失败（按未登录返回）: %s", e)
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


def _require_login_fast(account_id: str = "default") -> None:
    """同步版登录熔断（供同步路由使用）：只读 15s 缓存 + 风控状态，绝不连 Chrome。

    为什么不用 async 版：publish 路由是同步函数（内含 2–5s 拟人停顿），
    改成 async 会让这段停顿阻塞整个事件循环，拖慢所有轮询与截图请求。
    缓存未命中视为「未知」放行——发布真发不出去时平台会给出真实反馈，
    不值得为一次前置检查把服务卡住。
    """
    blocked, reason = risk.is_blocked(account_id)
    if blocked:
        raise HTTPException(403, reason)
    st = _login_status_from_cache(time.time())
    if st and st.get("status") == "not_logged_in":
        raise HTTPException(
            403,
            "小红书登录态已失效：请在 app 内登录小窗完成登录（扫码/手机号/微信任一途径）后重试",
        )


# ================= 任务式采集（开始采集 / 进度 / 停止） =================
# 采集与展示分离：采集是后台异步任务（滚动平台搜索页逐批提取，支持中途停止），
# 采满目标数 / 平台不再增长 / 用户停止 之后统一落库并评分排序，前端再展示。
# 这样避免"边采边排"导致的列表跳变（先采集后排序，样本固定排序才稳定）。
import uuid

_COLLECT_TASKS: dict = {}


# 只表达变化，不做周期采样；SSE 的 30s ping 属传输层保活，不承载业务状态。
_EVENT_SUBS: list = []
_EVENT_QUEUE_MAX = 200


def _state_snapshot(state: dict) -> dict:
    """采集任务的可推送快照（前端据此渲染按钮与进度）。"""
    return {
        "task_id": state.get("task_id", ""),
        "status": state.get("status", ""),
        "phase": state.get("phase", ""),
        "collected": state.get("collected", 0),
        "saved": state.get("saved", 0),
        "limit": state.get("limit", 0),
        "keyword": state.get("keyword", ""),
        "note": state.get("note", ""),
        "need_login": state.get("status") == "ended_need_login",
        "filter_warnings": state.get("filter_warnings", []),
    }


def _emit_event(kind: str, data: dict | None = None) -> None:
    """广播一条事件给所有订阅者。只在状态真正变化时调用（无轮询）。"""
    for q in list(_EVENT_SUBS):
        try:
            q.put_nowait({"kind": kind, "data": data or {}})
        except asyncio.QueueFull:  # 订阅者积压：丢最旧语义，不阻塞生产者
            pass


def emit_collect(state: dict) -> None:
    """推送采集状态（暂停/恢复/进度/收尾共用同一形状）。"""
    _emit_event("collect", _state_snapshot(state))


_NOTES_UPSERT_SQL = (
    "INSERT OR REPLACE INTO notes (feed_id, keyword, title, note_type, user_id,"
    " nickname, avatar, cover_url, xsec_token, desc, ip_location, liked_count,"
    " collected_count, comment_count, shared_count, fans_count, publish_time,"
    " score, tag, fan_ratio, fetched_at, score_info, video_url, account_id, origin,"
    " hot_score, hot_info, live_photo, live_videos, detail_imgs)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _rank_and_insert(con, n: dict, keyword: str, now: str, account_id: str = "default") -> bool:
    """单条笔记评分 + 落库（带账号维度）。返回是否成功。

    唯一写库点：只挡缺 feed_id 的脏数据。点赞数不做入库判定——筛选排序已经
    决定先采谁，再拿点赞数做硬门槛只会把冷门词下本来就不多的候选一并挡掉。
    """
    # apiNotes 图片名生成 500，前端收尾拉列表失败误判任务结束复位按钮
    # （「没点停止按钮却变开始采集，调试窗口还在采集」的状态错位）。
    if not n.get("feed_id"):
        return False
    try:
        n["keyword"] = keyword
        n["local_cover"] = ""
        si, hi = _score_pair(n)
        con.execute(_NOTES_UPSERT_SQL, _notes_row(n, keyword, si, hi, now, account_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def _score_pair(n: dict) -> tuple[dict, dict]:
    """双口径评分：低粉分（排序依据）与综合热度分，任一失败都退化为 0 分标记。"""
    try:
        si = rank_notes([n])[0]["score_info"]
    except Exception:  # noqa: BLE001
        si = {"score": 0.0, "tag": "评分失败", "fan_ratio": 0.0,
              "dimensions": {"like_fan_ratio": 0.0, "low_fan": 0.0,
                             "interaction_health": 0.0, "freshness": 0.0}}
    try:
        hi = score_note_hot(n)  # 第二口径（综合热度），与低粉分同时入库
    except Exception:  # noqa: BLE001
        hi = {"score": 0.0, "tag": "评分失败", "fan_ratio": 0.0,
              "dimensions": {"like_scale": 0.0, "interaction": 0.0,
                             "reach": 0.0, "freshness": 0.0}}
    return si, hi


def _notes_row(n: dict, keyword: str, si: dict, hi: dict, now: str, account_id: str) -> tuple:
    return (
        n.get("feed_id"), keyword, n.get("title", ""), n.get("type", ""),
        n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
        n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""),
        n.get("ip_location", ""),
        _to_int(n.get("liked_count")), _to_int(n.get("collected_count")),
        _to_int(n.get("comment_count")), _to_int(n.get("shared_count")),
        fans_for_db(n.get("fans_count")), n.get("publish_time"),
        si["score"], si["tag"], si["fan_ratio"],
        now, json.dumps(si, ensure_ascii=False), n.get("video_url", ""),
        account_id or "default", n.get("origin", ""),
        hi["score"], json.dumps(hi, ensure_ascii=False),
        int(n.get("live_photo") or 0),
        json.dumps(n.get("live_videos") or [], ensure_ascii=False),
        json.dumps(n.get("detail_imgs") or [], ensure_ascii=False),
    )

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
    """搜索/采集结果落库：按 feed_id 主键插入或更新（同批次重复采集不堆积）+ 本地评分。
    返回 (added, total, top)。整表清空由 /api/collect_clear 负责，本函数不删任何行。"""
    con = _db()
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


async def _enrich_fast(n: dict, cache: dict, account_id: str = "default") -> None:
    """快速档：直连详情页补互动 + 作者主页补粉丝（无拟人等待）。"""
    d = await browser_client.note(n.get("feed_id", ""), n.get("xsec_token", ""),
                                  wait_ms=2500, account_id=account_id)
    for k in ("liked_count", "collected_count", "comment_count", "shared_count", "user_id", "author_nick"):
        if d.get(k):
            n[k] = d[k]
    if d.get("author_avatar"):
        n["avatar"] = d["author_avatar"]
    if d.get("publish_time_ms"):
        n["publish_time"] = int(d["publish_time_ms"]) // 1000
    uid = n.get("user_id")
    if uid:
        await _fill_fans(n, uid, cache, account_id)


async def _fill_fans(n: dict, uid: str, cache: dict, account_id: str = "default") -> None:
    """作者粉丝数（按 uid 缓存；取不到记 None=未知，不写 0）。

    旧实现对 user() 传了不存在的 wait_ms，每次 TypeError 被 except 吞掉，
    快速档整批粉丝恒为 0 —— 低粉潜力 65 分权重凭空清零，榜单系统性失真。
    """
    if uid not in cache:
        try:
            u = await browser_client.user(uid, account_id=account_id)
            raw = (u.get("fans") or "").strip() if u else ""
            cache[uid] = _to_int(raw) if raw else None
        except Exception:  # noqa: BLE001
            cache[uid] = None
    n["fans_count"] = cache[uid]


async def _recover_collect_window(account_id: str) -> None:
    """停止后恢复采集窗口：清理残留小红书采集页（搜索/笔记/探索页）。

    复用 browser_client 清理逻辑（登录墙页保留）——确保停止后引擎不再有
    采集页在导航/滚动，CDP 行为完全停止；主应用窗口（8791）语义保持不变。
    """
    from src import browser_client
    try:
        await browser_client.recover_collect_window(account_id)
    except Exception:  # noqa: BLE001 尽力而为
        log.warning("停止后清理采集窗口失败", exc_info=True)


def _finalize_aborted(state: dict, emit: bool = False) -> bool:
    """关窗中断收尾：有 abort_note 就落状态并返回 True，调用方据此提前 return。

    关掉采集窗会让读页、导航、搜索结果全部退化成「失败」或「空」，
    底层据此给出的「需要登录 / 未采到数据 / 浏览器异常」都只是表象。用户口径是
    「关了就中断、要采就重来」，所以每一个收尾分支都必须先让位给 abort_note，
    否则用户会被误导去重新扫码，或者看到「未获取到笔记」这种错误归因。
    """
    note = state.get("abort_note")
    if not note:
        return False
    state["status"] = "stopped"
    state["phase"] = "done"
    state["note"] = note
    if emit:
        emit_collect(state)
    return True


def _finalize_stopped(state: dict, req, account_id: str) -> None:
    """框架级取消（用户点停止）收尾：已处理入库的条数即最终结果。

    流式流水线下数据逐条「补全→评分→入库」（state['saved'] 权威计数），
    取消时无需再批量抢救——「采集多少、处理多少、进入列表多少」恒等（所见即所得）；
    未处理的候选（含 search 已提取但未评分入库的）按用户口径不进入结果。
    """
    abort_note = state.get("abort_note")
    if abort_note:
        # 「已停止：采集到 N 条」——否则用户会以为自己点了停止。
        _finalize_aborted(state)
        return
    processed = state.get("saved", 0)
    state["status"] = "stopped"
    state["note"] = f"已停止：采集到 {processed} 条" if processed else "已停止（未采到结果）"
    state["phase"] = "done"


_WIN_CLOSED_NOTE = "采集窗口已关闭，本次采集中断，请重新开始采集"


def _abort_one_task(st: dict, now: float) -> bool:
    """关窗回调对单个任务的动作；返回是否命中（命中即视为本次中断）。

    两种情况都算：正在跑的任务直接中止；刚结束（5 秒内）的任务补认原因——
    关窗瞬间 Playwright 会先一步让采集线程抛「Target closed」，等这里被调用时
    状态已落成 error，只按 running 判断会漏掉（用户看到「浏览器刚在重建」）。
    """
    if st.get("status") == "running":
        st["abort_note"] = _WIN_CLOSED_NOTE
        st["stop_event"] = True
        task = st.get("task")
        if task and not task.done():
            task.cancel()
        return True
    recent = now - st.get("ended_ts", now - 1e9) <= 5.0
    if st.get("status") not in ("error", "done") or st.get("abort_note") or not recent:
        return False
    st["abort_note"] = _WIN_CLOSED_NOTE
    st["status"] = "stopped"
    st["phase"] = "done"
    st["note"] = _WIN_CLOSED_NOTE
    emit_collect(st)
    return True


def abort_collect_on_window_close() -> int:
    """采集窗口被用户关闭 → 中止进行中的任务，并明确告知需重新采集。

    用户拍板口径：关掉的窗**不再自动重建**（自动重开窗口本身就是「守护」行为，
    与「完全抛弃守护、一问一答」冲突）。关窗即等于本次采集中断；要接着采就重新
    点「开始采集」——collect_start 经 _page → engine.new_page()，天然会新建一页。

    返回值：被中止的任务数（0 表示当时没有任务在跑）。
    """
    now = time.time()
    hit = sum(1 for st in list(_COLLECT_TASKS.values()) if _abort_one_task(st, now))
    if hit:
        log.info("采集窗口被关闭：已中止 %d 个进行中的采集任务", hit)
    return hit


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
    """新任务启动时优雅停止所有运行中的旧任务（非启动限制）。

    task.cancel() 框架级取消（旧任务所有 await 点被中断，立即收尾）。
    与 collect_stop 补齐同一条「停止」语义——旧任务真正结束后逐账号
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
    if _finalize_aborted(state):
        return
    if state["stop_event"] or res.get("stopped"):
        state["status"] = "stopped"
        state["note"] = "已停止（未采到结果）"
    else:
        # ——连续搜索限流/搜索页空壳时出现，用户据此稍后重试。
        state["status"] = "done"
        state["note"] = "未采集到数据（小红书限流或页面未加载出内容），请稍后重试"
    state["phase"] = "done"


def _collect_finalize(state: dict, req, res: dict) -> None:
    """流水线按序入库后的统一收尾：停止 / 采满 / 不足 三分支。

    「采集多少条、评分多少条、进入列表多少条」恒等（用户口径：所见即所得）。
    四个分支都必须落 status——漏设会让任务永远停在 running，而 running 是
    mirror 切换、keeper collect_win 治理、采集窗 关闭三处的唯一判据（状态泄漏会连锁污染壳层）。
    """
    if _finalize_aborted(state):
        return
    if state["stop_event"] or res.get("stopped"):
        state["status"] = "stopped"
        state["note"] = (
            f"已停止：采集到 {state['saved']} 条"
            if state["saved"] else "已停止（未采到结果）"
        )
    elif state["saved"] >= req.limit:
        state["status"] = "done"
        state["note"] = f"采集完成：{state['saved']} 条已按评分排序"
    elif state["saved"] > 0:
        state["status"] = "done"
        state["note"] = (
            f"采集完成：平台仅加载 {state['saved']} 条（不足 {req.limit}），已按评分排序"
        )
    else:
        state["status"] = "done"
        state["note"] = "未采集到有效数据（候选均缺少笔记 ID），请稍后重试"
    state["phase"] = "done"


# 「需要登录」与「采满」一样是终止条件；要接着采就重新发起一次采集。


def _collect_begin_round(state: dict) -> None:
    """采集开始前的阶段重置。"""
    state["phase"] = "search"
    state["collected"] = 0
    state["saved"] = 0
    state["partial_items"] = []


def _finalize_need_login(state: dict, account_id: str, err: Exception) -> None:
    """撞到登录墙：本次采集就此结束（已采条数保留），并立刻推事件通知左窗。

    必须推的原因：左窗完全靠事件驱动更新状态，不推就只能一直停在「正在采集」。
    不挂起等待、不续采——用户口径是「需要登录就重新采集」。
    """
    abort_note = state.get("abort_note")
    if abort_note:
        # 当成登录墙报上来。用户口径是「关了就中断、要采就重来」，提示必须是
        # 「窗口已关闭」——不能被「需要登录」覆盖，否则会误导用户去重新扫码。
        _finalize_aborted(state, emit=True)
        return
    saved = state.get("saved", 0)
    limit = state.get("limit", 0)
    state["status"] = "ended_need_login"
    state["phase"] = "done"
    goal = f" / 目标 {limit} 条" if limit else ""
    state["note"] = (
        f"需要登录，本次采集结束（已采 {saved} 条{goal}）" if saved
        else f"需要登录，本次采集结束（未采到数据{goal}）：{str(err)[:60]}"
    )
    emit_collect(state)


def _friendly_collect_error(e: Exception) -> str:
    """把底层浏览器异常翻成用户能照着做的下一步，不把 Playwright 原文丢到界面上。"""
    msg = str(e)
    if "has been closed" in msg:
        return "浏览器刚在重建（还在准备中），等十几秒再点一次「开始采集」"
    if "timeout" in msg.lower():
        return "等页面加载超时了：可能是网络慢，稍后再点一次「开始采集」"
    if "login" in msg.lower() or "登录" in msg:
        return "需要重新登录小红书：点右上角「登录」扫码后再采集"
    return f"采集失败：{msg[:100]}"


def _on_collect_progress(state: dict):
    """进度回调→事件推送：每采到一条推一次（变化驱动，不是定时轮询）。"""

    def _cb(n: int, it=None) -> None:
        state["collected"] = n
        if it is not None:
            state["partial_items"] = it
        emit_collect(state)

    return _cb


@_log.tagged("worker:collect:round")
async def _collect_round(state: dict, req, ctx: dict, account_id: str):
    """一轮「搜索 → 逐条补全入库」；撞墙抛 BrowserLoginRequiredError 给外层暂停。

    搜索页必须留到补全结束（keep_page）：详情只能在搜索页上点开，不能走 URL。
    返回搜索结果 dict；候选为空时已就地收尾并返回 None。
    """
    _collect_begin_round(state)
    try:
        res = await search_via_browser(
            req.keyword, req.limit,
            on_progress=_on_collect_progress(state),
            should_stop=lambda: state["stop_event"],
            filters={
                "sort": req.sort, "note_type": req.note_type,
                "time_range": req.time_range, "scope": req.scope, "distance": req.distance,
            },
            account_id=account_id,
            keep_page=True,
        )
        # 前端据此在 APP 窗口提示用户，而不是静默出一份没过滤的结果。
        if res.get("filter_warnings"):
            state["filter_warnings"] = res["filter_warnings"]
        candidates = res["items"]
        if not candidates:
            _collect_finalize_empty(state, res)
            return None
        await _collect_enrich_candidates(state, ctx, candidates)
        return res
    finally:
        # 采集一结束就释放，避免引擎常驻孤儿页。
        await browser_client.release_search_page(account_id)


async def _collect_enrich_candidates(state: dict, ctx: dict, candidates: list) -> None:
    """逐条补详情 → 评分 → 入库；用户停止即中断剩余。

    不做「已入库就跳过」的去重：点开始采集前 /api/collect_clear 已清空 notes，
    每批都是全新一批，续采跳过在标准流程下永不命中（用户拍板删除）。
    """
    state["phase"] = "enrich"
    emit_collect(state)  # 搜索转补全：立刻推一次，别让状态条卡在「已扫描 N 张」20 秒不动
    for item in candidates:
        if state["stop_event"]:
            break  # 停止：立即中断；已处理入库的保留，未处理的丢弃（所见即所得）
        await _collect_one(item, state, ctx)


@_log.tagged("worker:collect:retry")
async def _collect_run_rounds(state: dict, req, ctx: dict, account_id: str):
    """采集一轮；撞到登录墙即收尾结束（不重试、不暂停等待）。

    返回搜索结果 dict；已就地收尾（撞登录墙/空结果）时返回 None。
    """
    try:
        return await _collect_round(state, req, ctx, account_id)
    except BrowserLoginRequiredError as e:
        _finalize_need_login(state, account_id, e)
        return None


async def _collect_one(item: dict, state: dict, ctx: dict) -> None:
    """单条流水：补全详情 → 本地评分 → 入库 → 播报进度（一条一提交，停止即可见）。"""
    try:
        if ctx["req"].fast:
            await _enrich_fast(item, ctx["cache"], ctx["account_id"])
        else:
            item = await get_feed_detail(item, ctx["account_id"])
            item = await get_user_profile(item, ctx["cache"], ctx["account_id"])
    except BrowserLoginRequiredError:
        # 用户看到「采到了但内容不全」，比直接暂停更糟。
        raise
    except Exception:  # noqa: BLE001 单条补全失败不阻断，保留搜索页粗数据评分入库
        pass
    if _rank_and_insert(ctx["con"], item, ctx["req"].keyword, ctx["now"], ctx["account_id"]):
        ctx["con"].commit()  # 逐条提交：停止/取消时已入库的立即可见（所见即所得）
        state["saved"] += 1
        state["collected"] = state["saved"]
        state["partial_items"].append(item)
        state["note"] = f"已采集 {state['saved']} 条"
        # 逐条入库即推送（边沿触发回调，非轮询）：前端过程中看到数字/卡片逐条增长
        emit_collect(state)


@_log.tagged("worker:collect")
async def _collect_worker(state: dict, req, account_id: str) -> None:
    """采集 worker：多轮「搜索 → 逐条补全入库」。

    流式流水线：采满 limit 自动完成，停止时结果=已入库条数。
    （用户拍板：完全抛弃守护与等待）：撞到登录墙**立即收尾**——已入库部分
    按条数完成、状态 ended_need_login 并提示用户重新登录后重试；不挂起、不等待
    扫码、不自动续采、不自动导航/拉回。用户想继续就再点一次「开始采集」。
    """
    con = None
    try:
        set_rhythm(req.interval)
        con = _db()  # collect_start 已 _reset_collection_data 清空旧批次，此处只追加
        ctx = {
            "req": req, "account_id": account_id, "con": con, "cache": {},
            "now": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        res = await _collect_run_rounds(state, req, ctx, account_id)
        if res is None:
            return  # 已就地收尾（空结果/放弃/超时/账号受限），不再重复 finalize
        con.commit()
        state["phase"] = "save"
        _collect_finalize(state, req, res)
    except asyncio.CancelledError:
        _finalize_stopped(state, req, account_id)
        raise
    except Exception as e:  # noqa: BLE001 任务不因单点异常悬挂在 running
        if not _finalize_aborted(state):
            state["status"] = "error"
            state["note"] = _friendly_collect_error(e)
            state["phase"] = "done"
    finally:
        if con is not None:
            con.close()  # _db() 每次新建连接，结束即释放
        state["ended_ts"] = time.time()
        _mark_collect_done(account_id)  # 结束即记录，供节流闸使用
        emit_collect(state)  # 收尾一次推送：done/stopped/error 统一从事件到前端


@app.post("/api/collect_clear")
def collect_clear():
    """清空本批采集数据（笔记 + 采集条件 + 采集图片），独立于开始采集。

    客户库（clients 表）不清：它跨会话累积，要清走 POST /api/clients_clear。

    拆出来的原因：清空是破坏性动作，不该藏在「开始采集」里顺手做——
    外部脚本或别的入口调 collect_start 时不应被动清库。
    前端在点「开始采集」时先调本接口、再调 collect_start，行为与原来一致。
    """
    removed = _reset_collection_data()
    return {"ok": True, "images_removed": removed}


@app.post("/api/collect_start")
async def collect_start(req: SearchReq):
    """启动采集任务：后台滚动平台搜索页逐条提取→逐条补全评分入库，直到满额/平台不增长/用户停止。

    account_id 指定使用哪个账号引擎（空 = default）；多账户下采集号/发布号分离。
    """
    account_id = (req.account_id or "default").strip() or "default"
    _check_license_or_raise("collect")  # 授权薄层：collect 功能闸门（free 恒放行）
    await _require_login(account_id)
    # collect_start 自身不再清空——避免脚本或其他入口调它时被动删数据。
    # 被保留为「业务去重」，用户实操反馈同关键词刚结束立即重启被 409 拦截仍属限制）。
    # 同词/换词均允许立即重启；dedup_key 仅留作任务审计展示。
    await _stop_running_collect_tasks()
    tid = uuid.uuid4().hex[:12]
    state = {
        "status": "running", "keyword": req.keyword, "limit": req.limit,
        "collected": 0, "saved": 0,
        "note": "", "stop_event": False,
        "account_id": account_id, "dedup_key": f"{req.keyword.strip()}|{account_id}",
        "started_ts": time.time(),
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "phase": "search", "task_id": tid,
    }
    _COLLECT_TASKS[tid] = state
    # 记下本次筛选条件：点「采集客户」需要重开搜索页时必须带同样条件，否则原卡片不在结果里
    try:
        con = _db()
        con.execute(
            "INSERT OR REPLACE INTO collect_ctx (keyword, params, ts) VALUES (?,?,?)",
            (
                req.keyword.strip(),
                json.dumps(
                    {k: getattr(req, k, "") for k in
                     ("sort", "note_type", "time_range", "scope", "distance")},
                    ensure_ascii=False,
                ),
                datetime.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        con.commit()
        con.close()
    except sqlite3.Error as e:  # noqa: BLE001 记录失败不影响采集启动
        log.warning("collect_ctx 写入失败：%s", e)
    state["task"] = asyncio.create_task(_collect_worker(state, req, account_id))
    return {"ok": True, "task_id": tid, "keyword": req.keyword, "limit": req.limit,
            "account_id": account_id}


@app.get("/api/events")
async def events_stream():
    """SSE 事件流——前端从「定时问」改为「变化被通知」。

    只承载状态变化（collect/login/status）；30s 一次注释心跳仅用于保持连接，
    不携带业务状态，因此不构成业务轮询。
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX)
    _EVENT_SUBS.append(q)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 传输层保活
                    continue
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        finally:
            if q in _EVENT_SUBS:
                _EVENT_SUBS.remove(q)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/accounts")
async def accounts_list():
    """账号列表 + 每账号独占 CDP 端口 + 运行状态。

    多账户引擎池的数据源是 accounts.json；本接口把它连同 debug_port 一起暴露，
    供前端账号切换与 UI 自动化脚本「按账号连对应端口」使用（脚本不再硬编码 9223）。

    增补 running / headless：窗口管理必须看得出「这个账号现在开着没有」，
    否则界面上分不清「账号存在」与「引擎在跑」。
    """
    from src import engine_pool

    port_map = config.account_port_map()
    accounts = config.list_accounts()

    def _item(aid: str) -> dict:
        acc = accounts.get(aid) or {}
        return {
            "account_id": aid,
            # 界面主名称：自动派生（平台昵称 → 小红书号 → 内部 id），不需要人工起名
            "display_name": config.account_display_name(aid),
            # 人工备注（可选，只在界面作后缀补充用途）；没设过回退 id
            "name": config.account_name(aid),
            # 平台昵称：登录事件顺带缓存，用来一眼认出「这个号是谁」
            "nickname": config.account_nickname(aid),
            # 小红书号：用户能在 App「我」页面看到，唯一且稳定，用来核对
            "red_id": config.account_red_id(aid),
            "role": acc.get("role", "mixed"),
            "status": acc.get("status", "ok"),
            "debug_port": port_map.get(aid),
            "user_data_dir": acc.get("user_data_dir")
            or config.home_relative(config.default_user_data_dir()),
            "running": engine_pool.pool.peek(aid) is not None,
            "headless": config.account_headless(aid),
        }

    ids = list(accounts.keys())
    if "default" not in accounts:
        ids.insert(0, "default")
    return {"accounts": [_item(aid) for aid in ids],
            "port_map": port_map, "active": config.active_account()}


class AccountAddReq(BaseModel):
    account_id: str = ""
    name: str = ""
    role: str = "mixed"


class AccountRenameReq(BaseModel):
    name: str = ""


@app.post("/api/accounts")
async def accounts_add(req: AccountAddReq):
    """新增账号：自动分配独占 CDP 端口 + 相对数据目录的 profile。

    account_id 留空 = 自动生成 acc_xxxxxx；显式给名要满足 valid_account_id
    （小写字母/数字/下划线/短横线，≤32 字符——这是跨平台目录名约束）。
    name 是业务展示名（可中文），与目录名分离，改名不搬目录。
    """
    aid = (req.account_id or "").strip() or config.new_account_id()
    if aid in config.list_accounts():
        return {"ok": False, "error": f"账号已存在：{aid}"}
    try:
        acc = config.add_account(aid, role=req.role or "mixed", name=req.name)
    except ValueError as e:  # noqa: BLE001（id/业务名不合规：直接回给调用方，不抛 500）
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "account_id": aid,
        "name": acc.get("name") or aid,
        "role": acc.get("role"),
        "debug_port": config.account_debug_port(aid),
        # 与 GET /api/accounts 同口径：返回注册表里的相对路径（不是解析后的绝对路径）
        "user_data_dir": acc.get("user_data_dir"),
    }


@app.patch("/api/accounts/{account_id}")
async def accounts_rename(account_id: str, req: AccountRenameReq):
    """改账号业务名：只改展示名，不搬 profile 目录、不动登录态。

    account_id 是目录名，改名要迁移数据；业务名是纯展示字段，随时可改——
    这样用户能把 acc_a 标成「珠宝主号」而不用重新扫码。
    """
    if config.get_account(account_id) is None:
        return {"ok": False, "error": f"账号不存在：{account_id}"}
    name = (req.name or "").strip()
    if name and not config.valid_account_name(name):
        return {"ok": False, "error": (
            f"账号名最多 {config.ACCOUNT_NAME_MAX} 字符，不能含控制字符或路径分隔符")}
    if not config.set_account_name(account_id, name):
        return {"ok": False, "error": "改名失败"}
    return {"ok": True, "account_id": account_id, "name": config.account_name(account_id)}


async def accounts_refresh_identity(account_id: str):
    """识别账号身份：账号在跑时读一次 user/me，缓存昵称+小红书号+uid。

    为什么需要手动入口：这些标识默认由登录事件顺带写入，但在此机制上线**之前**
    就已登录的账号（如 default）没有缓存，列表里认不出是哪个号。
    不自动刷新的原因：每个账号要 reload 一次页面，账号多时开销与风控都不划算。
    """
    from src import engine_pool, login_session

    err = _identity_blocker(account_id, engine_pool)
    if err:
        return {"ok": False, "error": err}
    payload = await login_session.capture_user_me(account_id)
    err = _payload_blocker(login_session, payload)
    if err:
        return {"ok": False, "error": err}
    ident = login_session.cache_identity(account_id, payload)
    if not (ident["nickname"] or ident["red_id"]):
        return {"ok": False, "error": "已登录但未取到昵称与小红书号"}
    # 标题跟上身份：已开的窗口不用重开也能显示昵称 + 小红书号
    eng = engine_pool.pool.peek(account_id)
    if eng is not None:
        await eng.refresh_window_title()
    return {
        "ok": True,
        "account_id": account_id,
        "nickname": ident["nickname"],
        "red_id": ident["red_id"],
        "display_name": config.account_display_name(account_id),
    }


def _identity_blocker(account_id: str, engine_pool) -> str:
    """识别前置条件：账号存在、引擎在跑、当前没有采集任务。返回错误文案或空串。"""
    if config.get_account(account_id) is None:
        return f"账号不存在：{account_id}"
    if engine_pool.pool.peek(account_id) is None:
        return "账号引擎未运行，先开窗再识别"
    if _collect_running_for(account_id):
        return "有采集任务进行中，稍后再识别"
    return ""


def _collect_running_for(account_id: str) -> bool:
    return any(
        st.get("status") == "running" and st.get("account_id", "default") == account_id
        for st in list(_COLLECT_TASKS.values())
    )


def _payload_blocker(login_session, payload) -> str:
    """user/me 结果校验：没读到 / 未登录。返回错误文案或空串。"""
    if payload is None:
        return "未能读取账号信息（页面未响应或超时）"
    if not login_session.is_real_login_me(payload):
        return "该账号尚未登录，请扫码登录后再识别"
    return ""


@app.post("/api/accounts/{account_id}/scan_login")
async def accounts_scan_login(account_id: str):
    """扫码登录入口：无头账号先切有头，再打开登录页，一次调用完成。

    供未登录首次扫码、登录态过期重新扫码使用；default 承载壳窗本就有头，
    直接打开登录页。采集任务运行中不打断。
    """
    from src import engine_window

    return await engine_window.scan_login(account_id)


@app.delete("/api/accounts/{account_id}")
async def accounts_remove(account_id: str):
    """删除账号：引擎在跑先关掉；profile 移进回收区，不直接删。

    不直删的理由：profile 里有登录态，误删要重新扫码；回收区可回滚，
    也不会像「只删注册表」那样留下孤儿目录。
    """
    from src import engine_pool

    if account_id == "default":
        return {"ok": False, "error": "default 账号不可删除"}
    if account_id not in config.list_accounts():
        return {"ok": False, "error": f"账号不存在：{account_id}"}

    if engine_pool.pool.peek(account_id) is not None:
        await engine_pool.pool.close(account_id)
    retired = config.retire_account_dir(account_id)
    config.remove_account(account_id)
    note = (f"已删除；profile 已移入回收区 {config.home_relative(retired)}"
            if retired else "已删除（该账号没有 profile 目录）")
    return {"ok": True, "account_id": account_id, "note": note}


@app.get("/api/status_now")
async def status_now():
    """全局状态提示条（R 批 v1.4）：实时播报「正在干什么」，不落盘。

    数据源：采集任务注册表（_COLLECT_TASKS）→ 进行中则聚合动作文案+进度；否则空闲。
    与审计日志（logger.py 落盘账本）区分：本接口只服务前端实时播报。
    """
    running = [(tid, st) for tid, st in _COLLECT_TASKS.items() if st.get("status") == "running"]
    if running:
        tid, st = running[0]
        # 动作跟随真实阶段：搜索期说「正在搜索」，逐条入库期说「正在采集」。
        phase = st.get("phase", "")
        action = "正在搜索" if phase == "search" else "正在采集"
        return {
            "action": action,
            "detail": st.get("note") or "",
            "collected": st.get("collected", 0),
            "limit": st.get("limit", 0),
            "keyword": st.get("keyword", ""),
            # 接回正在跑的轮询，按钮恢复「停止」，不再出现「后端在采、按钮却说开始采集」
            "task_id": tid,
            "collecting": True,
        }
    return {"action": "空闲", "detail": "", "collecting": False}


@app.get("/api/collect_progress")
def collect_progress(task_id: str = ""):
    st = _COLLECT_TASKS.get(task_id)
    if not st:
        return {"ok": False, "note": "任务不存在或已过期"}
    # SSE 断线重连后补拉进度就识别不出「需要登录」。
    snap = _state_snapshot(st)
    snap.update({"ok": True, "account_id": st.get("account_id", "default")})
    return snap


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
        # （CDP 导航/提取/滚动/补全等），worker 捕获 CancelledError 收尾已采数据。
        # 相比逐点插 should_stop 检查（旧式散点补丁），任务级取消一处生效、无遗漏窗口。
        task.cancel()
    # 采集窗口 CDP 行为恢复由独立任务执行（不依赖被取消的 worker）：关闭残留
    # 搜索/笔记/探索页，停止后引擎不再有采集页在导航/滚动。
    account_id = st.get("account_id", "default")
    asyncio.create_task(_recover_collect_window(account_id))
    return {"ok": True, "note": "停止已请求，正在收尾…", "account_id": account_id}


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
        # 同分用点赞量兜底：粉丝数未知的笔记赞粉比不计分，同分时按绝对热度排，
        # 避免"同分时随机/插入序"看起来像没按分数排。
        sql += " ORDER BY score DESC, liked_count DESC"
    elif order == "hot":
        # 第二口径「综合热度」：点赞规模为主，同分仍按绝对点赞兜底。
        sql += " ORDER BY hot_score DESC, liked_count DESC"
    elif order == "time_asc":
        sql += " ORDER BY fetched_at ASC"  # 过程中：新卡片只往后追加、旧卡不动
    else:
        sql += " ORDER BY fetched_at DESC"
    sql += " LIMIT 200"
    return sql, params


def _decorate_note(d: dict) -> dict:
    """补两套评分明细（库值优先、老数据重算兜底）与本地封面路径。"""
    _attach_score_info(d)
    _attach_hot_info(d)
    cf = IMAGES_DIR / _img_name(d["feed_id"])
    d["local_cover"] = f"/app/images/{_img_name(d['feed_id'])}" if cf.exists() else ""
    return d


def _attach_hot_info(d: dict) -> None:
    """综合热度明细：优先读库 hot_info JSON；老数据（列为空）用现有字段重算兜底。"""
    si = None
    raw = d.get("hot_info")
    if raw:
        try:
            si = json.loads(raw)
        except Exception:  # noqa: BLE001
            si = None
    if not si:
        try:
            si = score_note_hot(d)
        except Exception:  # noqa: BLE001
            si = None
    if si:
        d["hot_info"] = si
        d["hot_score"] = si["score"]


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
    hot_count（低粉爆款🔥篇数）/ avg_score / max_score / total_liked / last_publish。
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
    save_path: str | None = None  # 用户在应用内「保存位置」对话框选定，非空则直接写盘


class SaveTextReq(BaseModel):
    path: str
    content: str


@app.get("/api/save_places")
def save_places():
    """导出对话框的常用位置（下载/桌面/文档/上次用过）。"""
    return _save_dialog.places()


@app.get("/api/list_dirs")
def list_dirs(path: str = ""):
    """目录浏览：列出子目录供应用内「保存位置」对话框导航。"""
    try:
        return _save_dialog.list_dirs(path or str(Path.home()))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/save_text")
def save_text(req: SaveTextReq):
    """把前端生成的文本（JSON/CSV）写到用户选定的路径。"""
    existed = Path(req.path).expanduser().is_file()
    try:
        target = _save_dialog.write_bytes(req.path, req.content.encode("utf-8"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ok": True,
        "path": str(target),
        "bytes": target.stat().st_size,
        "overwrote": existed,
    }


def _download_remote_cover(con, zf, fid: str) -> bool:
    """远程 cover_url 补下载入包，成功返回 True。"""
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
    """单篇笔记入包（本地缓存优先，缺失回退远程 cover_url）。"""
    prefix = fid.replace("#", "_")
    files = sorted(IMAGES_DIR.glob(f"{prefix}_*.jpg")) if IMAGES_DIR.exists() else []
    if files:
        for f in files:
            zf.write(f, arcname=f.name)
        return
    if _download_remote_cover(con, zf, fid):
        return
    missing.append(fid)


def _pack_live_videos(zf, con, fid: str) -> int:
    """实况图（动图）原片入包：按库内 live_videos 下载那几段 3 秒 mp4，与图片同包交付。

    动图原片是 sns-video CDN 上的真实 mp4（详情页那个 blob: 只是本地播放地址，不可下载）。
    本地已缓存直接复用；单段失败跳过，不影响整包。返回成功入包段数。
    """
    urls = _live_video_urls(con, fid)
    if not urls:
        return 0
    prefix = fid.replace("#", "_")
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    packed = 0
    for i, url in enumerate(urls[:9]):
        dest = VIDEOS_DIR / f"{prefix}_live{i}.mp4"
        try:
            _ensure_live_video(url, dest)
            if dest.exists() and dest.stat().st_size >= 1024:
                zf.write(dest, arcname=dest.name)
                packed += 1
        except Exception:  # noqa: BLE001
            continue  # 单段失败跳过（CDN 过期/网络抖动）
    return packed


def _live_video_urls(con, fid: str) -> list:
    """库里的实况图原片地址列表；无记录/脏 JSON 一律当空。"""
    try:
        row = con.execute("SELECT live_videos FROM notes WHERE feed_id = ?", (fid,)).fetchone()
        urls = json.loads((row["live_videos"] if row else "") or "[]")
    except Exception:  # noqa: BLE001
        return []
    return urls if isinstance(urls, list) else []


def _ensure_live_video(url: str, dest) -> None:
    """本地没有才下载（带 Referer/UA，CDN 对裸请求 403）。"""
    if dest.exists():
        return
    reqh = urllib.request.Request(
        url,
        headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(reqh, timeout=30) as resp:
        dest.write_bytes(resp.read())

@app.post("/api/download_images")
def download_images(req: DownloadImgsReq):
    """打包选中笔记的本地图片为 zip，直接以文件流返回（不落盘）。

    前端拿到 application/zip 后用 File System Access API 弹系统「另存为」，
    由用户自己选择保存位置；缺失的 feed_id 通过 X-Missing 响应头回传。

    优先打包本地缓存（克隆时下载的 fid_pN.jpg / fid_cover.jpg 全部命中）；
    本地无缓存时回退远程 cover_url 补下载（带 Referer，低频，仅用户主动点打包时触发）。
    """
    if not req.feed_ids:
        raise HTTPException(400, "没有选中的笔记")
    missing: list[str] = []
    live_packed = 0
    con = _db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in req.feed_ids:
            _pack_feed_images(zf, con, fid, missing)
            live_packed += _pack_live_videos(zf, con, fid)
    data = buf.getvalue()
    if not data:
        raise HTTPException(400, "所选笔记没有可打包的图片")
    fname = f"xhs_images_{int(time.time() * 1000)}.zip"
    if req.save_path:
        # 用户已在应用内「保存位置」对话框选好路径：直接写盘，不再经过浏览器
        existed = Path(req.save_path).expanduser().is_file()
        try:
            target = _save_dialog.write_bytes(req.save_path, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            "ok": True,
            "path": str(target),
            "bytes": len(data),
            "missing": missing,
            "count": len(req.feed_ids) - len(missing),
            "live": live_packed,
            "overwrote": existed,
        }
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    if missing:
        headers["X-Missing"] = ",".join(missing)
    return Response(content=data, media_type="application/zip", headers=headers)


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
    local_images = _download_images(req.feed_id, images)

    n["images_local"] = local_images

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


def _norm_img_urls(v) -> list:
    """把库里存的 JSON 字符串或列表统一成去空白的 URL 列表。"""
    if isinstance(v, str):
        try:
            v = json.loads(v or "[]")
        except Exception:  # noqa: BLE001
            v = []
    if not isinstance(v, list):
        return []
    return [u.strip() for u in v if isinstance(u, str) and u.strip()]


async def _collect_images(n: dict, feed_id: str) -> list:
    """克隆用：取详情图片列表（本地浏览器提取，取不到才回退库里详情图，再退封面）。

    两个坑（真机实测：高赞图文笔记克隆出 0 张图）：
    1. 库里 cover_url 是采集当时的签名地址，隔几天必然 403 —— 只能当兜底，不能当主路径；
    2. 详情页 DOM 图懒加载，刚打开时 src 为空 —— 所以取图以详情返回的列表为准，
       并在这里把空串/空白串过滤掉，别让空地址流到下载环节变成 [''] 这种假结果。
    """
    n, images = await _detail_images(n)
    if not images:
        # 克隆时常常没有活动搜索页（规则也不许直接按 URL 开详情），详情取不到；
        # 采集那一刻的详情图已经存进库，签名还有效，用它比用封面靠谱得多。
        images = _norm_img_urls(n.get("detail_imgs"))
    if not images:
        images = _cover_fallback(n)
    log.info("克隆取图：source=%s urls=%d feed=%s", n.get("img_source") or "-", len(images), feed_id)
    return images


async def _detail_images(n: dict) -> tuple[dict, list]:
    """详情页优先：取到图顺带回填标题/正文（详情 SSR 更准）；失败不改 n、返回空图。"""
    try:
        dn = dict(n)
        dn = await get_feed_detail(dn)
        imgs = _norm_img_urls(dn.get("detail_imgs"))
        if imgs:
            if dn.get("desc"):
                n["desc"] = dn.get("desc")
            if dn.get("title"):
                n["title"] = dn.get("title")
            return n, imgs
    except Exception:  # noqa: BLE001
        pass
    return n, []


def _cover_fallback(n: dict) -> list:
    cover = (n.get("cover_url") or "").strip()
    return [cover] if cover else []

def _download_image(feed_id: str, idx: int, url: str) -> str:
    """克隆用：下载单张图到本地。返回可访问路径（失败/非图片响应/装饰性小图返回空串）。"""
    if not url or not str(url).startswith("http"):
        return ""
    fname = _img_name(feed_id, suffix=f"p{idx}")
    dest = IMAGES_DIR / fname
    try:
        if not dest.exists() and not _fetch_image(url, dest):
            return ""
        if _is_decorative(dest):
            dest.unlink()
            return ""
        return f"/app/images/{fname}"
    except Exception:  # noqa: BLE001
        return ""


def _fetch_image(url: str, dest) -> bool:
    """抓一张图落盘；Content-Type 不是 image/* 视为过期签名（403/HTML 页），返回 False。"""
    reqh = urllib.request.Request(
        url,
        headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(reqh, timeout=20) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if not ctype.startswith("image/"):
            return False
        dest.write_bytes(resp.read())
    return True


def _is_decorative(dest) -> bool:
    """装饰性小图（头像/图标 2-4KB）：正文原图一般 ≥50KB，<20KB 判定为非正文图。"""
    return dest.exists() and dest.stat().st_size < 20 * 1024

def _download_images(feed_id: str, images: list) -> list:
    """克隆用：并行下载图片（≤9 张）。返回本地路径列表（失败的图不占位）。"""
    urls = [u for u in images[:9] if isinstance(u, str) and u.strip()]
    with ThreadPoolExecutor(max_workers=6) as ex:
        paths = list(ex.map(lambda p: _download_image(feed_id, p[0], p[1]), enumerate(urls)))
    ok = [p for p in paths if p]
    log.info("克隆下载：尝试=%d 成功=%d feed=%s", len(urls), len(ok), feed_id)
    return ok


# ================= 采集客户（P4） =================
class ClientsReq(BaseModel):
    feed_id: str
    xsec_token: str = ""
    resume: bool = False      # 上一次到上限后，接着采下一批（沿用同一笔记页）
    limit: int = 150          # 单次条数上限（0 = 用后端默认）


@app.post("/api/clients")
async def collect_clients(req: ClientsReq):
    """采集笔记评论区互动用户名单（O1-3）：真实浏览器打开原作品页提取评论用户（含属地/时间），
    意向分级（需求词命中法）后入库去重。

    图文与视频笔记均支持（评论区都有互动用户）。每次采集访问一次真实页面，频次受浏览器全局锁约束。
    风控处理：触发验证码/风险页时返回 429（已采部分保留，前端提示停用 ≥24h，绝不自动重试）。
    """
    await _require_login()
    from src import browser_client  # 提前导入：except 分支要用 ClientCollectError
    from src import clients_flow
    plan = _clients_plan(req)
    try:
        got = await browser_client.clients(
            req.feed_id, plan["token"], keyword=plan["keyword"], filters=plan["filters"],
            limit=plan["limit"], resume=req.resume, known=plan["known"])
    except HTTPException as e:
        # 风控风险页：已采部分保留（历史入库记录不动），明确提示停用窗口，不自动重试
        raise HTTPException(429, "触发平台风险拦截，建议停用 ≥24h，人工确认后再恢复采集") from e
    except browser_client.BrowserLoginRequiredError as e:
        # 被平台判异常访问时已自动退登，前端据此提示"重新扫码登录"
        raise HTTPException(409, str(e)) from e
    except browser_client.ClientCollectError as e:
        # 前置条件不满足（无搜索页 / 卡片已不在结果里）：409 让界面照原话提示，不伪装成"没人评论"
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"浏览器采集失败：{e}") from e
    con = _db()
    try:
        users, added = clients_flow.ingest(
            con, req.feed_id, plan["author_uid"], got.get("users") or [],
            datetime.datetime.now().isoformat(timespec="seconds"))
    finally:
        con.close()
    return clients_flow.summarize(
        users, plan["declared"], bool(got.get("capped")), plan["limit"], added)


def _clients_plan(req: ClientsReq) -> dict:
    """DB 读写留在这里，业务装配交给 clients_flow（纯函数、可单测）。"""
    from src import clients_flow
    con = _db()
    try:
        return clients_flow.build_plan(con, req.feed_id, req.xsec_token, req.resume, req.limit)
    finally:
        con.close()


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
    # 「想要关注」「XXX关注」是关注动作记录，不是评论内容，不参与线索统计
    where.append("comment NOT LIKE '%关注'")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    # 档位数值（CASE 映射）：高4 > 上3 > 中2 > 低1；每人取最高档位（同档取最新）
    intent_rank_sql = (
        "CASE intent WHEN 'high' THEN 4 WHEN 'upper' THEN 3 WHEN 'middle' THEN 2 ELSE 1 END"
    )
    sql = (
        "SELECT uid, MAX(nickname) AS nickname, COUNT(*) AS comment_count, "
        "COUNT(DISTINCT feed_id) AS note_count, "
        # 一人多条评论全部带出去（用户拍板：有几条就写几条，只留一条会丢掉后一条需求信号）
        "GROUP_CONCAT(comment, char(31)) AS comments_raw, "
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
    # 不再按 200 截断：total 必须是真实去重人数，列表也全量下发——
    # 截断会让「本篇 N 位客户」在超过 200 人时变成假的 200。
    out = []
    for r in ordered:
        d = dict(r)
        raw = d.pop("comments_raw", "") or ""
        d["comments"] = [c for c in raw.split(chr(31)) if c]
        out.append(d)
    return {"total": len(out), "clients": out}


class ClientsClearReq(BaseModel):
    feed_id: str = ""


@app.post("/api/clients_clear")
def clients_clear(req: ClientsClearReq | None = None):
    """显式清空客户库（跨会话累积的唯一清空入口）。

    带 feed_id 只清某篇笔记的客户，不带则清空全库。
    客户数据默认跨天/跨会话累积，所以不做自动清理，避免误伤历史线索。
    """
    feed_id = (req.feed_id if req else "").strip()
    con = _db()
    try:
        if feed_id:
            cur = con.execute("DELETE FROM clients WHERE feed_id = ?", (feed_id,))
        else:
            cur = con.execute("DELETE FROM clients")
        con.commit()
        return {"ok": True, "removed": cur.rowcount or 0}
    finally:
        con.close()


class ContactOpenReq(BaseModel):
    profile: str


class FillXhsReq(BaseModel):
    title: str = ""
    desc: str = ""
    images: list[str] = []


@app.post("/api/fill_xhs")
async def fill_xhs(req: FillXhsReq):
    """把准备好的内容填进小红书发布页（创作者中心「上传图文」表单）。

    只填不发布：最后那一下「发布」由用户在小红书里手动点（产品红线）。
    可见范围 / 发布账号由平台自己管，不在这里设置。
    """
    await _require_login()
    from src import browser_client

    body = (req.desc or "").strip()
    files = [_local_image_path(u) for u in (req.images or [])]
    files = [f for f in files if f]
    try:
        note = await browser_client.fill_write_page(req.title, body, files)
    except browser_client.FillXhsError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"浏览器操作失败：{e}") from e
    return {"ok": True, "note": note}


def _local_image_path(u: str) -> str:
    """把 /app/images/xxx 或 /api/img?path=... 解析成本地图片绝对路径；解析不到返回空串。"""
    name = _image_name_from_url(u)
    if not name:
        return ""
    p = (IMAGES_DIR / name).resolve()
    try:
        p.relative_to(IMAGES_DIR.resolve())
    except ValueError:
        return ""  # 越出图片目录（../ 穿越）一律拒绝
    return str(p) if p.exists() else ""


def _image_name_from_url(u: str) -> str:
    """从两种前端地址里抠文件名；抠不到返回空串。"""
    from urllib.parse import unquote
    s = (u or "").strip()
    if not s:
        return ""
    if "/app/images/" in s:
        return s.split("/app/images/", 1)[1].split("?")[0]
    if "path=" not in s:
        return ""
    name = unquote(s.split("path=", 1)[1]).split("&")[0]
    return name.split("/app/images/", 1)[1] if "/app/images/" in name else name

@app.post("/api/contact_open")
async def contact_open(req: ContactOpenReq):
    """O1-5 去联系：在采集窗打开用户主页并点「聊天」按钮，落到私信会话页（工具不代发消息）。

    任一步没落地返回 409，让前端照原话提示，不静默假装成功。
    """
    await _require_login()
    from src import browser_client
    try:
        chat_url = await browser_client.open_contact(req.profile)
    except browser_client.ContactOpenError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"浏览器操作失败：{e}") from e
    return {
        "ok": True,
        "chat_url": chat_url,
        "note": "已打开私信会话页，工具不代写、不代发：请自行沟通，注意平台私信限额与导流词红线（详见「获客指引」）",
    }


# ================= 客户库聚合（O1-4）：线索/意向分布/属地TOP/近7天/留联系方式 =================
# ================= Cookie（显示 / 一键从 Chrome 导入） =================
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


# ---- 分析图片（vision）：与生文、生图各自独立的第三套配置 ----
# 三种能力依赖的模型完全不同（文本模型不认图、图像模型只出图），故配置必须分开存。
@app.get("/api/vision_config")
def get_vision_config():
    """读取「分析图片」配置（key 掩码）。"""
    return llm_service.public_config("vision")


@app.post("/api/vision_config")
def set_vision_config(req: LLMConfigReq):
    """保存「分析图片」配置。api_key 为空时保留旧 key。"""
    if not req.openai_base_url or not req.default_model:
        raise HTTPException(400, "请填写 Base URL 与模型名称")
    return llm_service.save_config(req.api_key, req.openai_base_url, req.default_model, "vision")


@app.post("/api/vision_config/clear")
def clear_vision_config():
    """清除「分析图片」配置。"""
    return llm_service.clear_config("vision")


@app.post("/api/vision_test")
async def test_vision(req: LLMConfigReq):
    """测试「分析图片」供应商连通性（GET /models）。"""
    return await llm_service.test_with_saved(req.api_key, req.openai_base_url, req.default_model, "vision")


# ================= O1 账号人设（AI 生成口吻；v1.5 设置页三卡之一） =================
# 存储：<数据目录>/persona.json（随 DATA_DIR 走，升级换包不丢，不写进安装目录）。
PERSONA_PATH = DATA_DIR / "persona.json"


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
    """组装系统提示（基础人设 + 已配置账号人设注入）。"""
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


# ================= AI 生图配置（与生文分离；只列能出图的模型） =================
from src import ai_image  # noqa: E402
from src import options_store  # noqa: E402
from src import profiles_store  # noqa: E402
from src import templates_store  # noqa: E402


class ImageConfigReq(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""


@app.get("/api/ai_image_config")
def get_image_config():
    """生图配置（key 掩码）+ 供应商清单（内含各自可出图模型）。"""
    return ai_image.public_config()


@app.post("/api/ai_image_config")
def set_image_config(req: ImageConfigReq):
    if not req.model:
        raise HTTPException(400, "请选择生图模型")
    return ai_image.save_config(req.provider, req.api_key, req.base_url, req.model)


@app.post("/api/ai_image_config/clear")
def clear_image_config():
    return ai_image.clear_config()


@app.post("/api/ai_image_test")
async def test_image(req: ImageConfigReq):
    return await ai_image.test_connection(req.provider, req.api_key, req.base_url)


class ImageGenReq(BaseModel):
    prompt: str = ""
    size: str = ""
    n: int = 1


@app.post("/api/ai_image_generate")
async def generate_note_image(req: ImageGenReq):
    """真实生图：按已配置供应商出 3:4 竖图，返回图片 URL（当前仅接入阿里云百炼）。"""
    _check_license_or_raise("llm")
    if not req.prompt.strip():
        raise HTTPException(400, "缺少生图提示词")
    try:
        urls = await ai_image.generate_image(
            req.prompt.strip(),
            size=req.size or ai_image.PORTRAIT_34,
            n=max(1, min(req.n, 4)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "urls": urls}


# ================= 账号档案（图文生成的事实底座） =================
class ProfileReq(BaseModel):
    id: str = ""
    name: str = ""
    nickname: str = ""
    brand: str = ""
    industry: str = ""
    audience: str = ""
    selling_points: str = ""
    products: str = ""
    address: str = ""
    tone: str = ""
    avoid_words: str = ""


@app.get("/api/profiles")
def get_profiles():
    return {"ok": True, "items": profiles_store.list_profiles()}


@app.post("/api/profiles")
def save_profile(req: ProfileReq):
    row = profiles_store.save_profile(req.model_dump())
    return {"ok": True, "item": row}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    if not profiles_store.delete_profile(profile_id):
        raise HTTPException(404, "档案不存在")
    return {"ok": True}


class AiOptionsReq(BaseModel):
    industries: list[str] | None = None
    tones: list[str] | None = None


@app.get("/api/ai_options")
def get_ai_options():
    """行业 / 口吻的下拉候选（含默认集，供「恢复默认」按钮用）。"""
    return {"ok": True, **options_store.load(), "defaults": options_store.defaults()}


@app.post("/api/ai_options")
def save_ai_options(req: AiOptionsReq):
    return {"ok": True, **options_store.save(req.model_dump(exclude_none=True))}


@app.post("/api/ai_options/reset")
def reset_ai_options():
    return {"ok": True, **options_store.reset()}


# ================= 内容模板（文字模板 + 图片模板） =================
class TextTemplateReq(BaseModel):
    id: str = ""
    name: str = ""
    desc: str = ""
    prompt: str = ""
    scene: str = ""


class ImageTemplateReq(BaseModel):
    id: str = ""
    name: str = ""
    skeleton: str = ""
    skin: dict | None = None


@app.get("/api/content_templates")
def get_content_templates():
    """两类模板全量；首次启动后端回落默认（5 文字 + 6 图片）。"""
    return {"ok": True, **templates_store.load()}


@app.post("/api/content_templates/text")
def save_text_template(req: TextTemplateReq):
    try:
        row = templates_store.save_text(req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@app.post("/api/content_templates/image")
def save_image_template(req: ImageTemplateReq):
    payload = req.model_dump()
    payload["skin"] = req.skin or {}
    try:
        row = templates_store.save_image(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@app.delete("/api/content_templates/{kind}/{template_id}")
def delete_content_template(kind: str, template_id: str):
    if kind == "text":
        ok = templates_store.delete_text(template_id)
    elif kind == "image":
        ok = templates_store.delete_image(template_id)
    else:
        raise HTTPException(400, "模板类型只能是 text 或 image")
    if not ok:
        raise HTTPException(404, "模板不存在")
    return {"ok": True}


@app.post("/api/content_templates/reset")
def reset_content_templates():
    return {"ok": True, **templates_store.reset()}


@app.post("/api/content_templates/reset/{kind}")
def reset_one_group(kind: str):
    try:
        return {"ok": True, **templates_store.reset_group(kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class OutlineReq(BaseModel):
    profile_id: str = ""
    structure: str = ""      # 内容结构名（如「避坑清单」「对比测评」）
    word_count: int = 400    # 目标正文字数（200/400/600，向导第一步三档，默认400）
    note: dict = {}          # {title, desc, keyword} 原笔记信息


_OUTLINE_SPEC = """输出严格 JSON（不要代码块、不要解释），结构：
{"title": "标题（≤20字，含1个emoji）",
 "body": "正文（双换行分段，每段1-2个emoji开头，前3行放核心，字数遵守下面的目标字数）",
 "tags": "#标签1 #标签2 #标签3（3-8个，精准在前泛话题在后）",
 "pages": [{"type":"cover","heading":"封面主标题≤9字","copy":"封面副标题"},
           {"type":"content","heading":"内页小标题","copy":"2-3行内容页文案"},
           {"type":"content","heading":"...","copy":"..."},
           {"type":"content","heading":"...","copy":"..."}]}
要求：pages 恰好 4 页（1 封面 + 3 内容页）；内容只能来自档案和原笔记，禁止编造价格、地址、体验。"""


_WC_SPEC = {200: "200 字以内，简短直接", 400: "400 字左右", 600: "600 字以内，可充分展开"}


def _parse_json_block(text: str) -> dict:
    """容错解析模型输出：去掉 ``` 围栏，截取首个 { 到末个 }。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    return json.loads(s)


async def run_outline(profile_id: str, structure: str, word_count: int, note: dict) -> dict:
    """生文核心：返回大纲 dict（title/body/tags/pages），供 ai_outline 路由与一键串联复用。"""
    if not llm_service.enabled():
        raise ValueError("未配置 AI 模型：请先在「设置 · AI」配置生文模型")
    profile = profiles_store.get_profile(profile_id) or profiles_store.default_profile()
    title = (note.get("title") or "")[:500]
    desc = (note.get("desc") or "")[:2000]
    keyword = (note.get("keyword") or "")[:200]
    structure = (structure or "避坑清单")[:40]
    target = word_count if word_count in _WC_SPEC else 400
    system = "\n".join([
        "你是小红书图文创作专家，只输出可直接发布的文案，拒绝“宝子们/家人们/谁懂啊”式通用AI味。",
        profiles_store.profile_prompt_block(profile),
    ]).strip()
    prompt = (
        f"参考的原笔记（只借主题与角度，不要照抄）：\n标题：{title}\n正文：{desc}\n"
        f"品类关键词：{keyword}\n内容结构：{structure}\n"
        f"目标正文字数：{_WC_SPEC[target]}；正文实际字数请贴近该目标，不要明显超出。\n\n"
        "请按该结构规划一篇全新的小红书图文笔记。\n" + _OUTLINE_SPEC
    )
    raw = await llm_service.chat(system, prompt, max_tokens=2400, temperature=0.8)
    return _parse_json_block(raw)


@app.post("/api/ai_outline")
async def ai_outline(req: OutlineReq):
    """一句话 → 可编辑大纲（标题/正文/标签/分页文案）。人在大纲阶段先改，再花钱生图。"""
    _check_license_or_raise("llm")
    try:
        data = await run_outline(req.profile_id, req.structure, req.word_count, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"大纲解析失败：{e}") from e
    profile = profiles_store.get_profile(req.profile_id) or profiles_store.default_profile()
    return {"ok": True, "outline": data, "profile_id": (profile or {}).get("id", "")}


def _page_image_prompt(page: dict, profile: dict, index: int) -> str:
    """把单页文案转成生图视觉提示词：不让模型出文字（中文小字易糊），文字后期 HTML 叠加。"""
    industry = profile.get("industry") or "珠宝"
    products = profile.get("products") or ""
    heading = page.get("heading", "")
    if page.get("type") == "cover" or index == 0:
        return (
            f"小红书封面，{industry}门店高级感实拍风格，主体：{products or industry}，"
            "3:4竖构图，柔和自然光，浅景深，背景干净，顶部与底部留白便于后期加标题，"
            "画面不要出现任何文字、字母或水印"
        )
    return (
        f"小红书内页配图，{industry}主题：{heading}，相关元素：{products or industry}，"
        "3:4竖构图，色调统一、干净通透，一屏一个视觉重点，"
        "画面不要出现任何文字、字母或水印"
    )


async def _images_for_pages(pages: list[dict], profile: dict) -> list[str]:
    """逐页构造提示词并生图（封面+内容页，3:4），供一键串联与按大纲配图复用。"""
    images = []
    for i, page in enumerate((pages or [])[:4]):
        prompt = _page_image_prompt(page, profile, i)
        images.extend(await ai_image.generate_image(prompt, n=1))
    return images


class OutlineImageReq(BaseModel):
    profile_id: str = ""
    pages: list[dict] = []


@app.post("/api/ai_image_from_outline")
async def image_from_outline(req: OutlineImageReq):
    """按已编辑大纲逐页生图（人改完大纲再配图），返回与 pages 对齐的 3:4 图片路径。"""
    _check_license_or_raise("llm")
    if not (req.pages or []):
        raise HTTPException(400, "缺少分页内容")
    profile = (
        profiles_store.get_profile(req.profile_id)
        or profiles_store.default_profile()
        or {}
    )
    try:
        images = await _images_for_pages(req.pages, profile)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "images": images}


# ================= 静态前端 =================
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
# 本地封面图静态服务（xhscdn 图床按 Referer 防盗链，本地代理同源访问无 403）
app.mount("/app/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
# 本地视频静态服务（视频克隆下载产物，供创作页预览/后续发布上传）
app.mount("/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="videos")


@app.get("/")
def index():
    # 禁止缓存 index.html：升级换包后浏览器缓存会继续拉旧的 assets 名，
    # 界面便一直跑上一版旧代码（2026-11-23 实测：新包在跑，页面仍加载旧 bundle）。
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})
