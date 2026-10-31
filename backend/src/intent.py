"""O1 采集客户：评论意向分级器（需求词命中法）。

依据 docs/project/O1需求文档.md F3：高意向(留联系方式/问价购买) → 上意向(问方法/渠道)
→ 中意向(表态互动) → 低意向(纯水)。词表可配：matched_word 记录命中词，方便用户看懂分级原因。
顺序检查：高 → 上 → 中（低为兜底）。
"""
from __future__ import annotations

# 默认需求词表（v1.2 F3）。命中任一即入该档。
# 注意：个别词跨档（如"求链接"在视频形态下常含购买意图），规则引擎按高→上→中依次匹配，
# 词表中不重复收录，取第一命中档。
HIGH_WORDS: tuple[str, ...] = (
    # 留联系方式（含变体）
    "vx", "v信", "卫星", "weixin", "wechat", "微信", "薇信", "私我", "私聊我",
    "滴滴", "私信", "si我", "si 我", "手机", "电话", "+86",
    # 问价/购买
    "多少钱", "价格", "哪里买", "在哪买", "哪里能买", "怎么买",
    "下单", "已买", "怎么订", "预订", "预定", "买它", "已下单", "买了",
)
UPPER_WORDS: tuple[str, ...] = (
    "怎么做", "怎么做的", "求教程", "求方法", "求分享", "求经验", "怎么用",
    "怎么弄", "有推荐吗", "推荐", "怎么练", "减了多久", "怎么区分", "问下",
    "求链接", "链接", "在哪里", "哪里买得到", "怎么得到", "怎么领取", "怎么获得",
    "什么牌子", "什么型号", "什么店", "哪家店", "在哪家", "地址", "店铺",
    "具体说说", "展开说说", "详细说说", "想了解一下", "想了解", "想知道",
)
MIDDLE_WORDS: tuple[str, ...] = (
    "想要", "想买", "求", "羡慕", "好看想", "想尝试", "我也想", "收藏了",
    "想要同款", "同款", "种草", "喜欢这个", "真好看想",
)


def grade_comment(comment: str) -> dict:
    """分级一条评论：返回 {intent, matched_word, score}。

    intent ∈ high/upper/middle/low；matched_word 为命中的第一个词（low 为空）。
    """
    text = (comment or "").lower()
    # 高 → 上 → 中，顺序检查（low 兜底）
    for intent, words in (("high", HIGH_WORDS), ("upper", UPPER_WORDS), ("middle", MIDDLE_WORDS)):
        for w in words:
            if w.lower() in text:
                return {"intent": intent, "matched_word": w, "score": _SCORE[intent]}
    return {"intent": "low", "matched_word": "", "score": _SCORE["low"]}


# 档位 → 展示名映射（前端/聚合用）
INTENT_LABEL: dict[str, str] = {"high": "高意向", "upper": "上意向", "middle": "中意向", "low": "低意向"}
_INTENT_ORDER: tuple[str, ...] = ("high", "upper", "middle", "low")

# 档位权重（聚合/排序用：高>上>中>低）
_SCORE: dict[str, int] = {"high": 4, "upper": 3, "middle": 2, "low": 1}


def intent_rank(intent: str) -> int:
    """档位数值（高=4 … 低=1），用于聚合排序与最高档位判定。"""
    return _SCORE.get(intent, 1)


def max_intent(a: str, b: str) -> str:
    """两档取更高档（聚合用户多次评论时用）。"""
    return a if intent_rank(a) >= intent_rank(b) else b


def grade_batch(rows: list[dict]) -> list[dict]:
    """批量分级评论行（原地附 intent/matched_word/ip_location/comment_time）。"""
    for r in rows:
        r.update(grade_comment(r.get("comment", "")))
    return rows


# O1-4 聚合：留联系方式词（HIGH_WORDS 中联系方式子集，命中即"留联系方式"）
CONTACT_WORDS: tuple[str, ...] = (
    "vx", "v信", "卫星", "weixin", "wechat", "微信", "薇信", "私我", "私聊我",
    "滴滴", "私信", "si我", "si 我", "手机", "电话", "+86",
)


def is_contact(comment: str, matched_word: str = "") -> bool:
    """是否留了联系方式（评论原文或命中词含联系方式词）。"""
    if matched_word and matched_word in CONTACT_WORDS:
        return True
    text = (comment or "").lower()
    return any(w in text for w in CONTACT_WORDS)
