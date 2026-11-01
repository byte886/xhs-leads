"""M3/M4: 采集 + 评分 + 落库流水线（对 xiaohongshu-mcp HTTP MCP 的封装）。

用法:
  python3 collect_pipeline.py "翡翠" [--limit 50] [--out /path/db.sqlite3]

流程:
  1. search_feeds 搜索关键词 → 解析 feed 卡片（互动数据 + xsec_token）
  2. 对每条候选调 get_feed_detail 拿发布时间 + user_profile 拿粉丝数（按作者缓存）
  3. score.rank_notes 评分排序
  4. 落 SQLite（notes 表 + 评分字段）
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

# 允许从任意 cwd 导入 score 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.score import rank_notes, _to_int  # noqa: E402

REQUEST_INTERVAL = (1.0, 1.8)  # 真人浏览节奏：请求间随机 1-1.8s（用户拍板合理像人档，勿再调快）
SESSION_MAX_NOTES = 20        # 单次采集会话最多 20 条（真人一次浏览的量级，勿一次拉太多）


def _parse_likes(s: str) -> int:
    """解析互动数：'151'→151, '1.2万'→12000, '3千'→3000, '999+'→999"""
    if not s:
        return 0
    s = s.strip().replace(",", "")
    m = re.match(r"([\d.]+)(万|千|w|W)?", s)
    if not m:
        return 0
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "千":
        v *= 1000
    elif unit:
        v *= 10000
    return int(v)


async def search_via_browser(keyword: str, limit: int = 20, on_progress=None, should_stop=None,
                             filters: dict | None = None, account_id: str = "default") -> dict:
    """本地浏览器搜索（Playwright 复用账号引擎连接，DOM 层提取卡片）。

    任务式采集：支持进度回调 on_progress(n) 与中途停止 should_stop()。
    filters：搜索筛选（排序/笔记类型/发布时间/搜索范围/位置距离），透传浏览器应用。
    account_id：使用哪个账号引擎采集（默认 default；多账户下采集号/发布号分离）。
    返回 {"items": notes 列表, "stopped": bool}。
    """
    from src import browser_client

    data = await browser_client.search(
        keyword, limit,
        on_progress=on_progress,
        should_stop=should_stop,
        filters=filters,
        account_id=account_id,
    )
    items = data.get("items", [])[:limit]
    notes = []
    for it in items:
        href = it.get("href") or ""
        xsec = ""
        mm = re.search(r"xsec_token=([^&]+)", href)
        if mm:
            xsec = mm.group(1)
        # author 形如 "昵称\n09-22"，去掉日期
        author = (it.get("author") or "").replace("\n", " ")
        nickname = re.sub(r"\s\d{2}-\d{2}$", "", author).strip() or author
        notes.append({
            "feed_id": it.get("id"),
            "xsec_token": xsec,
            "title": it.get("title", ""),
            "type": it.get("type", ""),
            "user_id": None,
            "nickname": nickname,
            "avatar": "",
            "liked_count": _parse_likes(it.get("likes", "")),
            "shared_count": 0,
            "comment_count": 0,
            "collected_count": 0,
            "cover_url": it.get("img", ""),
        })
    return {"items": notes, "stopped": data.get("stopped", False)}


async def _run_browser(cmd: str, arg: str, tag: str, arg2: str = "", account_id: str = "default",
                       wait_ms: int | None = None) -> dict | None:
    """经常驻账号引擎连接调浏览器执行（note/user），复用连接不弹窗。失败返回 None。"""
    from src import browser_client

    try:
        if cmd == "note":
            return (await browser_client.note(arg, arg2, wait_ms=wait_ms or 5000,
                                              account_id=account_id)) or None
        if cmd == "user":
            return (await browser_client.user(arg, wait_ms=wait_ms or 5000,
                                              account_id=account_id)) or None
        return None
    except Exception:  # noqa: BLE001
        return None


async def get_feed_detail(note: dict, account_id: str = "default") -> dict:
    """补全笔记详情（本地浏览器打开笔记页 DOM+SSR 提取标题/正文/作者/日期/图片/互动数据）。

    互动数据（赞/藏/评/转/发布时间/作者 uid）来自页面 SSR 注入的 __INITIAL_STATE__
    （无需签名接口，比 DOM 抓取更可靠）。图片仍走 DOM 轮播图选择器。
    account_id：使用哪个账号引擎（默认 default；多账户下采集号/发布号分离）。
    """
    d = await _run_browser("note", note.get("feed_id", ""), "NOTE",
                           note.get("xsec_token", ""), account_id,
                           wait_ms=3000)
    if d:
        _merge_detail_core(note, d)
        _merge_detail_interactions(note, d)
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return note


async def get_note_detail_by_url(url: str, account_id: str = "default") -> dict:
    """数据回流：按完整笔记链接抓取详情（已发布笔记）。返回提取原始 dict，未 merge。"""
    from src import browser_client

    try:
        d = await browser_client.note_url(url, account_id=account_id)
    except Exception:  # noqa: BLE001
        return {}
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return d or {}


def _merge_detail_core(note: dict, d: dict) -> None:
    """标题/正文/类型 + 无标题兜底 + 作者 uid/昵称 + 详情图片。"""
    if d.get("title"):
        note["title"] = d["title"]
    if d.get("desc"):
        note["desc"] = d["desc"]
    if d.get("type"):
        # 详情页视频判断更准：二次修正 note_type（搜索卡片 play-icon 可能漏判）
        note["note_type"] = d["type"]
    # 无标题笔记兜底：取正文去掉话题标记后的首段前 20 字
    if not note.get("title") and note.get("desc"):
        dd = note["desc"].strip().lstrip("#").strip()
        dd = re.sub(r"\[.*?\]", "", dd).strip()
        note["title"] = dd[:20]
    # 作者 uid 补全（搜索页 user_id 恒 None → 详情页 SSR 提供）：
    # 有了 uid，get_user_profile 才能拿到粉丝数（低粉判断的前提）
    if d.get("user_id"):
        note["user_id"] = d["user_id"]
    if d.get("author_nick"):
        note["nickname"] = d["author_nick"]
    note["detail_imgs"] = d.get("imgs", [])
    # 视频原地址（视频克隆下载用；图文笔记恒空）
    note["video_url"] = d.get("video_url", "")


def _merge_detail_interactions(note: dict, d: dict) -> None:
    """互动数据补全（SSR 来源，详情页比搜索卡片更全更准）+ 发布时间转换。"""
    for k in ("liked_count", "collected_count", "comment_count", "shared_count", "views"):
        if d.get(k):
            note[k] = d[k]
    # 发布时间：SSR time 为毫秒时间戳 → 秒级；DOM 日期（如 09-22）无年份不做时间戳
    ms = d.get("publish_time_ms")
    if ms:
        try:
            note["publish_time"] = int(ms) // 1000
        except (TypeError, ValueError):
            note["publish_time"] = None


def _parse_fans(text: str) -> int:
    """解析粉丝数文本：'1.2万'→12000、'3000'→3000、'12.3w'→123000。"""
    f = (text or "").replace(",", "")
    m = re.match(r"([\d.]+)(万|w|W)?", f)
    if not m:
        return 0
    v = float(m.group(1))
    return int(v * 10000 if m.group(2) else v)


async def get_user_profile(note: dict, cache: dict, account_id: str = "default") -> dict:
    """拿作者粉丝数（本地浏览器打开作者主页 DOM 提取；按 user_id 缓存）。
    account_id：使用哪个账号引擎（默认 default）。"""
    uid = note.get("user_id")
    if not uid:
        return note
    if uid in cache:
        note["fans_count"] = cache[uid]
        return note
    u = await _run_browser("user", uid, "USER", "", account_id, wait_ms=3500)
    fans = 0
    if u:
        fans = _parse_fans(u.get("fans") or "")
        if not fans and u.get("nickname"):
            note["nickname"] = u["nickname"]
    cache[uid] = fans
    note["fans_count"] = fans
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return note


def init_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            feed_id TEXT PRIMARY KEY,
            keyword TEXT,
            title TEXT,
            note_type TEXT,
            user_id TEXT,
            nickname TEXT,
            avatar TEXT,
            cover_url TEXT,
            xsec_token TEXT,
            desc TEXT,
            ip_location TEXT,
            liked_count INTEGER,
            collected_count INTEGER,
            comment_count INTEGER,
            shared_count INTEGER,
            fans_count INTEGER,
            publish_time INTEGER,
            score REAL,
            tag TEXT,
            fan_ratio REAL,
            fetched_at TEXT,
            score_info TEXT,
            video_url TEXT
        )
    """)
    return con


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keywords", nargs="+", help="搜索关键词，可多个（如 翡翠 玉镯）")
    parser.add_argument("--limit", type=int, default=SESSION_MAX_NOTES, help=f"单会话采集上限（默认 {SESSION_MAX_NOTES}，真人浏览节奏）")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "data" / "xhs_notes.db"))
    args = parser.parse_args()
    import asyncio
    asyncio.run(_main_cli(args))


def _upsert_note(con: sqlite3.Connection, n: dict, keyword: str, now: str) -> None:
    """CLI 落库单条（评分字段来自 n['score_info']）。"""
    si = n["score_info"]
    con.execute(
        "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            n.get("feed_id"), keyword, n.get("title", ""), n.get("type", ""),
            n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
            n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""), n.get("ip_location", ""),
            _to_int(n.get("liked_count")), _to_int(n.get("collected_count")),
            _to_int(n.get("comment_count")), _to_int(n.get("shared_count")),
            _to_int(n.get("fans_count")), n.get("publish_time"),
            si["score"], si["tag"], si["fan_ratio"],
            json.dumps(si, ensure_ascii=False), now, n.get("video_url", ""),
        ),
    )
    con.commit()


async def _cli_search(args) -> list:
    """CLI 阶段 1：多关键词搜索 + 去重。返回合并后的笔记列表。"""
    print(f"[1/4] 多关键词搜索: {args.keywords}")
    all_notes = []
    seen = set()
    for kw in args.keywords:
        try:
            notes_kw = await search_via_browser(kw, args.limit)
            for n in notes_kw:
                if n["feed_id"] not in seen:
                    seen.add(n["feed_id"])
                    n["keyword"] = kw
                    all_notes.append(n)
            print(f"      「{kw}」→ +{len(notes_kw)} 条，累计去重 {len(all_notes)} 条")
        except Exception as e:  # noqa: BLE001
            print(f"      「{kw}」失败: {e}")
        time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    notes = all_notes[: args.limit]
    print(f"      → 最终取 {len(notes)} 条（去重后）")
    return notes


async def _cli_enrich(con: sqlite3.Connection, notes: list, keyword: str) -> list:
    """CLI 阶段 2：详情+粉丝补全 → 评分 → 落库。返回已落库的评分列表。"""
    import datetime

    now = datetime.datetime.now().isoformat(timespec="seconds")
    existing = {r[0] for r in con.execute("SELECT feed_id FROM notes").fetchall()}
    todo = [n for n in notes if n["feed_id"] not in existing]
    print(f"[2/4] 详情+粉丝补全（待处理 {len(todo)} 条，已跳过 {len(notes)-len(todo)} 条）...")
    cache = {}
    for i, n in enumerate(todo):
        try:
            n = await get_feed_detail(n)
            n = await get_user_profile(n, cache)
            n["score_info"] = rank_notes([n])[0]["score_info"]
            _upsert_note(con, n, keyword, now)
            if (i + 1) % 10 == 0 or (i + 1) == len(todo):
                print(f"      → {i+1}/{len(todo)}（库内累计 {con.execute('SELECT COUNT(*) FROM notes').fetchone()[0]} 条）")
        except Exception as e:  # noqa: BLE001
            print(f"      → 第 {i+1} 条失败（{n.get('title','')[:16]}）: {e}，继续下一批")
            time.sleep(5)
    con.commit()
    ranked = rank_notes(todo)
    print(f"      → 已完成落库，库内共 {con.execute('SELECT COUNT(*) FROM notes').fetchone()[0]} 条")
    return ranked


async def _main_cli(args):
    """CLI 入口：搜索 → 补全评分落库 → 打印 TOP10。"""
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    con = init_db(Path(args.out))

    notes = await _cli_search(args)
    ranked = await _cli_enrich(con, notes, args.keywords[0])

    print("\n=== TOP 10 评分结果（本次新增） ===")
    for n in ranked[:10]:
        si = n["score_info"]
        print(f"  [{si['tag']}] {si['score']:5.1f}分  赞{n.get('liked_count')} 粉{n.get('fans_count')}  {n.get('title','')[:24]}")
    con.close()


if __name__ == "__main__":
    main()
