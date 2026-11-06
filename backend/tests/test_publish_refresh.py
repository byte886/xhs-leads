"""主页按标题定位笔记：_pick_card 的匹配规则（刷新数据靠它找对那一条）。"""
from src.browser_client import _pick_card, _norm_text


def _card(i: int, text: str) -> dict:
    return {"i": i, "text": text.replace("\n", " "), "x": 100, "y": 100, "w": 200, "h": 300}


CARDS = [
    _card(0, "已违规\n测试-勿公开\n普通人\n赞"),
    _card(1, "仅自己可见\n转租福田区宝鸿苑合租单间 1400\n普通人\n1"),
    _card(2, "AI游戏\n普通人\n6"),
    _card(3, "一份AI agent的上手练习手册大纲\n普通人\n1"),
]


def test_match_exact_title():
    assert _pick_card(CARDS, "AI游戏")["i"] == 2


def test_match_title_with_status_line():
    """卡片首行是状态（仅自己可见），标题在第二行，也要能对上。"""
    assert _pick_card(CARDS, "转租福田区宝鸿苑合租单间 1400")["i"] == 1


def test_match_truncated_title_by_head():
    """主页卡片可能把长标题截断，用前 10 字兜底。"""
    assert _pick_card(CARDS, "一份AI agent的上手练习手册大纲（完整版）")["i"] == 3


def test_no_match_returns_none():
    assert _pick_card(CARDS, "完全不存在的标题") is None


def test_empty_title_returns_none():
    assert _pick_card(CARDS, "") is None
    assert _pick_card(CARDS, "   ") is None


def test_normalize_strips_separators():
    assert _norm_text(" AI 游戏 ") == "AI游戏"
