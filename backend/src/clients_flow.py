"""采集客户（P4）业务：计划装配 / 过滤入库 / 结果汇总。

server 侧只保留 HTTP 外壳（登录校验、浏览器调用、异常映射）；
本模块不 import src.server（DB 连接由调用方传入），避免循环依赖。
"""

from __future__ import annotations

import json

from src.intent import INTENT_LABEL, grade_batch

DEFAULT_LIMIT = 150
LIMIT_MIN = 20
LIMIT_MAX = 500
INTENT_RANK = {"high": 0, "upper": 1, "middle": 2, "low": 3}
COVERAGE_MIN_DECLARED = 30   # 声明数太小（<30）时覆盖率判断没有意义
COVERAGE_MIN_RATIO = 0.9     # 低于声明数 90% → 多半是采集中途被打断

INSERT_CLIENT_SQL = (
    "INSERT INTO clients "
    "(uid, feed_id, nickname, comment, profile, created_at, intent, matched_word, ip_location, comment_time) "
    "VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(uid, feed_id, comment) DO UPDATE SET "
    "intent=excluded.intent, matched_word=excluded.matched_word, "
    "ip_location=excluded.ip_location, comment_time=excluded.comment_time"
)


def is_noise_comment(text: str) -> bool:
    """关注动作记录（「昵称关注」/「想要关注」）不是评论内容，不入客户线索。"""
    t = (text or "").strip()
    return bool(t) and len(t) <= 24 and t.endswith("关注")


def dedupe_by_uid(users: list) -> list:
    """把评论记录按 uid 收敛成「人」：一人可发多条评论，界面只算一个人。

    实测口径差：平台声明 488 条评论 → 浏览器抓到 465 条评论记录 → 实际 376 人。
    同一人保留意向最高的那条（high > upper > middle > low）。
    """
    by_uid: dict = {}
    for u in users:
        k = u.get("uid") or ""
        cur = by_uid.get(k)
        if cur is None or _rank(u) < _rank(cur):
            by_uid[k] = u
    return list(by_uid.values())


def _rank(user: dict) -> int:
    return INTENT_RANK.get(user.get("intent", "low"), 9)


def build_plan(con, feed_id: str, xsec_token: str = "", resume: bool = False, limit: int = 0) -> dict:
    """装配一次采集计划：token/关键词兜底、作者 uid、筛选条件、续采已知用户、单批条数。"""
    meta = _note_meta(con, feed_id, xsec_token)
    return {
        **meta,
        "filters": _filters_of(con, meta["keyword"]),
        "known": _known_users(con, feed_id) if resume else [],
        "limit": _batch_limit(limit),
    }


def _note_meta(con, feed_id: str, token_in: str) -> dict:
    """笔记侧兜底：explore/{id} 直链会 404，必须带 xsec_token；keyword 供重开搜索页。"""
    row = con.execute(
        "SELECT xsec_token, keyword, user_id, comment_count FROM notes WHERE feed_id=?",
        (feed_id,),
    ).fetchone()
    if not row:
        return {"token": token_in, "keyword": "", "author_uid": "", "declared": 0}
    return {
        "token": token_in or (row[0] or ""),
        "keyword": row[1] or "",
        "author_uid": row[2] or "",
        "declared": int(row[3] or 0),
    }


def _batch_limit(limit: int) -> int:
    """单批条数可由界面调（默认 150）：太小要多点几次，太大容易超时/踩风控，掐在 20~500。"""
    return max(LIMIT_MIN, min(LIMIT_MAX, int(limit or DEFAULT_LIMIT)))


def _filters_of(con, keyword: str) -> dict:
    """采集条件（来自 collect_ctx.params）；无记录或脏数据一律当空筛选。"""
    row = con.execute("SELECT params FROM collect_ctx WHERE keyword=?", (keyword,)).fetchone()
    if not (row and row[0]):
        return {}
    try:
        return json.loads(row[0]) or {}
    except ValueError:
        return {}


def _known_users(con, feed_id: str) -> list:
    rows = con.execute("SELECT uid, comment FROM clients WHERE feed_id=?", (feed_id,)).fetchall()
    return [{"uid": r[0], "comment": r[1]} for r in rows]


def ingest(con, feed_id: str, author_uid: str, users: list, ts: str) -> tuple[list, int]:
    """过滤噪声与作者本人 → 意向分级 → 入库去重。返回（保留的评论记录, 新增行数）。"""
    keep = [
        u for u in users
        if not is_noise_comment(u.get("comment", ""))
        and (u.get("uid") or "") != author_uid
    ]
    grade_batch(keep)
    added = 0
    for u in keep:
        added += con.execute(INSERT_CLIENT_SQL, _row_values(u, feed_id, ts)).rowcount
    con.commit()
    return keep, added


def _row_values(u: dict, feed_id: str, ts: str) -> tuple:
    return (
        u.get("uid", ""), feed_id, u.get("nickname", ""), u.get("comment", ""),
        u.get("profile", ""), ts, u.get("intent", "low"), u.get("matched_word", ""),
        u.get("ip_location", ""), u.get("comment_time", ""),
    )


def summarize(users: list, declared: int, capped: bool, limit: int, added: int) -> dict:
    """结果汇总：人数/意向分布/剩余量/分段与覆盖标记（口径注释见各 helper）。"""
    people = dedupe_by_uid(users)
    total = len(users)
    remain = max(0, declared - total)
    return {
        "total": total,
        "people": len(people),
        "declared": declared,
        "capped": capped,
        "limit": limit,
        "remain": remain,
        "added": added,
        "stalled": _stalled(capped, added, remain),
        "more": bool(not capped and remain > 0),
        "low_coverage": _low_coverage(capped, declared, total),
        "new": added,
        "users": users,
        "summary": _intent_summary(people),
    }


def _intent_summary(people: list) -> dict:
    summary: dict = {}
    for u in people:
        label = INTENT_LABEL.get(u.get("intent"), "低意向")
        summary[label] = summary.get(label, 0) + 1
    return summary


def _low_coverage(capped: bool, declared: int, total: int) -> bool:
    """明显低于平台声明数 → 提示「可能被打断」。

    声明数含已删评论与被折叠回复，正常覆盖 95% 左右（实测 95.3% / 95.9%）；
    到单批上限属「主动分段」，不能同时打 low_coverage（否则界面自相矛盾）。
    """
    if capped or declared < COVERAGE_MIN_DECLARED or total == 0:
        return False
    return total / declared < COVERAGE_MIN_RATIO


def _stalled(capped: bool, added: int, remain: int) -> bool:
    """没拿到新面孔、平台却还显示有剩余：多半是同一篇采太密被限频，不能当成功收尾。"""
    return bool(not capped and added == 0 and remain > 0)
