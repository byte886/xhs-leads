"""O1-3 意向分级器单元测试：档位判定 / 变体识别 / 顺序 / 批量。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))

from src.intent import (  # noqa: E402
    HIGH_WORDS, UPPER_WORDS, MIDDLE_WORDS,
    grade_comment, grade_batch, intent_rank, max_intent, INTENT_LABEL,
)


def test_high_contact_variants():
    """留联系方式变体全部判高意向，且记录命中词。"""
    for c in ("vx", "加微信", "卫星号", "私信我", "滴滴我", "手机号多少", "+86 我私你", "已私信你了"):
        r = grade_comment(c)
        assert r["intent"] == "high", (c, r)
        assert r["matched_word"], (c, r)


def test_high_price_buy():
    for c in ("多少钱", "这个价格多少", "怎么买", "哪里买", "想下单", "已买", "怎么预订"):
        r = grade_comment(c)
        assert r["intent"] == "high", (c, r)
        assert r["matched_word"]


def test_upper_question():
    for c in ("怎么做", "求教程", "求方法", "求分享", "怎么用", "有推荐吗", "怎么练的",
              "求链接", "什么牌子", "店铺在哪里", "具体说说", "想了解一下"):
        r = grade_comment(c)
        assert r["intent"] == "upper", (c, r)
        assert r["matched_word"]


def test_middle_interest():
    for c in ("想要", "想买", "求", "羡慕", "想尝试", "我也想要", "收藏了", "同款", "种草了"):
        r = grade_comment(c)
        assert r["intent"] == "middle", (c, r)


def test_low_water():
    for c in ("好看", "哈哈哈", "路过", "支持", "顶一下", "", "nice", "太棒了", "3", "学习了"):
        r = grade_comment(c)
        assert r["intent"] == "low", (c, r)
        assert r["matched_word"] == ""


def test_priority_high_beats_upper():
    """同一条评论先命中高意向词，即使也含上意向词（如"怎么做"）。"""
    r = grade_comment("怎么做的？可以加微信聊聊吗")
    assert r["intent"] == "high"
    assert r["matched_word"] == "微信"


def test_priority_upper_beats_middle():
    r = grade_comment("求教程，想要")
    assert r["intent"] == "upper"


def test_word_tables_nonempty():
    assert len(HIGH_WORDS) > 10 and len(UPPER_WORDS) > 10 and len(MIDDLE_WORDS) > 5
    # 词表无空串、无重复
    for words in (HIGH_WORDS, UPPER_WORDS, MIDDLE_WORDS):
        assert all(w.strip() for w in words)
        assert len(set(words)) == len(words)


def test_rank_and_max():
    assert intent_rank("high") == 4 > intent_rank("upper") == 3 > intent_rank("middle") == 2 > intent_rank("low") == 1
    assert max_intent("upper", "high") == "high"
    assert max_intent("low", "middle") == "middle"


def test_label_map():
    assert INTENT_LABEL["high"] == "高意向"
    assert INTENT_LABEL["upper"] == "上意向"
    assert INTENT_LABEL["middle"] == "中意向"
    assert INTENT_LABEL["low"] == "低意向"


def test_grade_batch_inplace():
    rows = [{"comment": "多少钱"}, {"comment": "好看"}]
    grade_batch(rows)
    assert rows[0]["intent"] == "high"
    assert rows[1]["intent"] == "low"
    assert rows[1]["matched_word"] == ""
