"""评分引擎：低粉爆款推荐（G5 自研核心）。

依据《评分模型-低粉爆款.md》(v1.0)：
  4 维度权重（默认值，可配置）：
    点赞粉丝比 40% / 低粉权重 25% / 互动健康度 20% / 时效性 15%
  分级规则（生财实战帖口径，可按行业调整）：
    点赞 ≤300           → 低赞（不入推荐）
    点赞>500 且 点赞/粉丝>3 → 低粉爆款 🔥
    粉丝 <500            → 低粉（最值得关注）

纯函数设计：输入一条"补全后的笔记记录"，输出 0-100 分数 + 标签，
不依赖网络/数据库，便于单元测试与后续加权重配置。
"""
from __future__ import annotations

from datetime import datetime, timezone

# 默认权重（可配置项，后续放 settings）
DEFAULT_WEIGHTS = {
    "like_fan_ratio": 0.40,
    "low_fan": 0.25,
    "interaction_health": 0.20,
    "freshness": 0.15,
}

# 默认阈值（来自生财实战帖，可按行业类目改数值）
DEFAULT_THRESHOLDS = {
    "low_like": 300,          # 点赞 ≤300 → 低赞
    "hot_like": 500,          # 点赞 >500 且 点赞/粉丝>3 → 低粉爆款
    "hot_ratio": 3.0,         # 点赞粉丝比阈值
    "low_fan": 500,           # 粉丝 <500 → 低粉
    "fresh_days": 30,         # 30 天内 → 时效满分
}

TAGS = {
    "low_fan_hit": "低粉爆款🔥",
    "low_fan": "低粉",
    "low_like": "低赞",
    "high_like": "高赞",
    "normal": "普通",
}


def _to_int(value: str | int | None, default: int = 0) -> int:
    """互动数字段在 MCP 返回里是 string，统一转 int。

    支持中文单位缩写："1.2万" → 12000、"3千" → 3000、"1.5亿" → 150000000。
    大 V 粉丝数常见"6.1万"形态，直接 int() 会炸。
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        pass
    unit = {"万": 10000, "千": 1000, "亿": 100000000}
    for suffix, mult in unit.items():
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mult)
            except ValueError:
                return default
    return default


def _days_since(publish_time: int | None, now: datetime | None = None) -> float:
    """publish_time 为秒级时间戳；返回发布距今的天数。"""
    if not publish_time:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromtimestamp(publish_time, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return float("inf")
    return (now - ts).total_seconds() / 86400.0


def _ratio_score(liked: int, fans: int, hot_ratio: float) -> tuple[float, float]:
    """维度 1：点赞粉丝比（40 分封顶）。返回 (得分, 原始比值)。"""
    fan_ratio = liked / fans if fans > 0 else (liked if liked > 0 else 0.0)
    return min(40.0, 40.0 * fan_ratio / hot_ratio), fan_ratio


def _low_fan_score(fans: int, low_fan: int) -> float:
    """维度 2：低粉权重（25 分：粉丝越少越高；500 粉丝以上线性衰减，5000 归零）。"""
    if fans == 0:
        return 0.0
    if fans < low_fan:
        return 25.0
    return max(0.0, 25.0 * (1 - (fans - low_fan) / 4500))


def _health_score(liked: int, collected: int, commented: int, shared: int) -> float:
    """维度 3：互动健康度（20 分：藏+评+转 相对点赞的活跃度；无赞不计分）。"""
    extra = collected + commented + shared
    if liked > 0 and extra > 0:
        return min(20.0, 20.0 * extra / liked)
    return 0.0


def _fresh_score(days: float, fresh_days: int) -> float:
    """维度 4：时效性（15 分：30 天内满分，之后线性衰减，90 天归零）。"""
    if days <= fresh_days:
        return 15.0
    return max(0.0, 15.0 * (1 - (days - fresh_days) / 60))


def _pick_tag(liked: int, fan_ratio: float, fans: int, t: dict) -> str:
    """分级标签（生财实战口径，优先级从高到低）。"""
    if liked <= t["low_like"]:
        return TAGS["low_like"]
    if liked > t["hot_like"] and fan_ratio > t["hot_ratio"]:
        return TAGS["low_fan_hit"]
    if fans < t["low_fan"]:
        return TAGS["low_fan"]
    if liked > t["hot_like"]:
        return TAGS["high_like"]
    return TAGS["normal"]


def score_note(
    note: dict,
    weights: dict | None = None,
    thresholds: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """对一条补全后的笔记打分。

    note 期望字段（与评分数据流水线输出对齐）：
      liked_count, collected_count, comment_count, shared_count  (int/str)
      fans_count      (int/str，来自 user_profile)
      publish_time    (int 秒级时间戳，来自 get_feed_detail)
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    liked = _to_int(note.get("liked_count"))
    collected = _to_int(note.get("collected_count"))
    commented = _to_int(note.get("comment_count"))
    shared = _to_int(note.get("shared_count"))
    fans = _to_int(note.get("fans_count"))
    publish_time = note.get("publish_time")

    ratio_score, fan_ratio = _ratio_score(liked, fans, t["hot_ratio"])
    low_fan_score = _low_fan_score(fans, t["low_fan"])
    health = _health_score(liked, collected, commented, shared)
    fresh_score = _fresh_score(_days_since(publish_time, now), t["fresh_days"])

    total = round(ratio_score + low_fan_score + health + fresh_score, 1)
    tag = _pick_tag(liked, fan_ratio, fans, t)

    return {
        "score": total,
        "tag": tag,
        "fan_ratio": round(fan_ratio, 2),
        "dimensions": {
            "like_fan_ratio": round(ratio_score, 1),
            "low_fan": round(low_fan_score, 1),
            "interaction_health": round(health, 1),
            "freshness": round(fresh_score, 1),
        },
    }


def rank_notes(notes: list[dict], **kwargs) -> list[dict]:
    """批量打分并按分数降序，返回带 score_info 的列表（不改原 dict）。"""
    ranked = []
    for note in notes:
        info = score_note(note, **kwargs)
        ranked.append({**note, "score_info": info})
    ranked.sort(key=lambda n: n["score_info"]["score"], reverse=True)
    return ranked
