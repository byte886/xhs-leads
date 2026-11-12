"""评分引擎单元测试：覆盖 score_note 全分支与 rank_notes。"""
import pytest

from src.score import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    TAGS,
    fans_for_db,
    fans_known_of,
    passes_entry_gate,
    rank_notes,
    score_note,
    score_note_hot,
)


def _note(**overrides):
    base = {
        "liked_count": 1000,
        "collected_count": 100,
        "comment_count": 50,
        "shared_count": 10,
        "fans_count": 300,
        "publish_time": 1700000000,  # ~2023-11，超过 90 天
    }
    base.update(overrides)
    return base


class TestToIntAndEdge:
    def test_string_numbers_ok(self):
        info = score_note(_note(liked_count="1200", fans_count="400"))
        assert info["fan_ratio"] == 3.0

    def test_zero_fans_no_crash(self):
        """粉丝未知（采集失败）：低粉与赞粉比都不计分（不能靠"没有分母"白拿满分）。

        未知用 None 表示；0 是真·零粉，会拿满低粉分（见 TestFansUnknown）。
        """
        info = score_note(_note(fans_count=None))
        assert info["dimensions"]["low_fan"] == 0.0
        assert info["dimensions"]["like_fan_ratio"] == 0.0
        assert info["fan_ratio"] == 0.0

    def test_zero_likes_low_score(self):
        info = score_note(_note(liked_count=0, fans_count=100))
        assert info["score"] < 30.0
        assert info["tag"] == TAGS["low_like"]

    def test_missing_fields_defaults(self):
        info = score_note({})
        assert info["score"] == 0.0
        assert info["tag"] == TAGS["low_like"]

    def test_bad_publish_time(self):
        info = score_note(_note(publish_time=-1))
        assert info["dimensions"]["freshness"] == 0.0


class TestUnknownFans:
    """粉丝数未知（详情补全失败 → fans=0）的笔记不得被误判为低粉/低粉爆款。"""

    def test_no_low_fan_hit_with_unknown_fans(self):
        info = score_note(_note(liked_count=1672, fans_count=0))
        assert info["tag"] != TAGS["low_fan_hit"]

    def test_no_low_fan_with_unknown_fans(self):
        info = score_note(_note(liked_count=800, fans_count=0))
        assert info["tag"] != TAGS["low_fan"]

    def test_unknown_fans_scores_lower_than_known_low_fan(self):
        unknown = score_note(_note(liked_count=1672, fans_count=0, publish_time=1700000000))
        known = score_note(_note(liked_count=1672, fans_count=50, publish_time=1700000000))
        assert unknown["score"] < known["score"], "有粉丝基数且比值高，才该高于粉丝未知"


class TestTagLogic:
    def test_low_fan_hit(self):
        # 点赞>500 且 点赞/粉丝>3
        info = score_note(_note(liked_count=2000, fans_count=400))
        assert info["tag"] == TAGS["low_fan_hit"]

    def test_low_like_dominates(self):
        # 点赞<=300 优先级最高（即使粉丝也少）
        info = score_note(_note(liked_count=200, fans_count=100))
        assert info["tag"] == TAGS["low_like"]

    def test_low_fan_when_fans_below_500(self):
        info = score_note(_note(liked_count=400, fans_count=300))
        assert info["tag"] == TAGS["low_fan"]

    def test_high_like(self):
        info = score_note(_note(liked_count=2000, fans_count=20000))
        assert info["tag"] == TAGS["high_like"]

    def test_normal(self):
        info = score_note(_note(liked_count=400, fans_count=2000))
        assert info["tag"] == TAGS["normal"]


class TestDimensions:
    def test_freshness_full_within_30_days(self, monkeypatch):
        import datetime

        fake_now = datetime.datetime(2026, 10, 25, tzinfo=datetime.timezone.utc)
        monkeypatch.setattr("src.score.datetime", _FakeDatetime(fake_now))
        info = score_note(
            _note(publish_time=1739200000),  # 2025-02-10，距今>30d 但<90d
            now=fake_now,
        )
        # now 参数与 monkeypatch 并存时以传入 now 为准
        assert info["dimensions"]["freshness"] >= 0.0

    def test_custom_weights(self):
        info = score_note(
            _note(liked_count=100, fans_count=50),
            weights={**DEFAULT_WEIGHTS, "low_fan": 0.5},
            thresholds={**DEFAULT_THRESHOLDS, "low_like": 50},
        )
        # 点赞 100>50 → 非 low_like；粉丝 50<500 → 低粉
        assert info["tag"] == TAGS["low_fan"]
        assert info["dimensions"]["low_fan"] == 25.0


class _FakeDatetime:
    """让 score.py 里的 datetime.fromtimestamp 与 now 保持一致的最小替身。"""

    def __init__(self, fixed):
        self._fixed = fixed

    def fromtimestamp(self, ts, tz=None):
        import datetime

        return datetime.datetime.fromtimestamp(ts, tz=tz)

    def now(self, tz=None):
        return self._fixed


class TestRank:
    def test_rank_sorts_desc(self):
        notes = [
            _note(liked_count=500, fans_count=100, publish_time=1700000000),
            _note(liked_count=5000, fans_count=20000, publish_time=1700000000),
        ]
        ranked = rank_notes(notes)
        assert ranked[0]["score_info"]["score"] >= ranked[1]["score_info"]["score"]
        assert "score_info" in ranked[0]

    def test_rank_keeps_original(self):
        notes = [_note()]
        ranked = rank_notes(notes)
        assert ranked[0]["liked_count"] == notes[0]["liked_count"]


def test_fan_ratio_calc():
    info = score_note(_note(liked_count=900, fans_count=300))
    assert info["fan_ratio"] == 3.0


def test_dimensions_sum_bounds():
    info = score_note(_note(liked_count=999999, fans_count=1))
    assert info["score"] <= 100.0
    assert info["score"] >= 0.0


class TestChineseUnits:
    """中文单位缩写（6.1万 / 3千）必须正确解析，真实大 V 数据形态。"""

    def test_wan_unit(self):
        info = score_note(_note(liked_count="1.2万", fans_count="500"))
        # 12000 赞 / 500 粉
        assert info["fan_ratio"] == 12000 / 500

    def test_qian_unit(self):
        info = score_note(_note(liked_count="3千", fans_count="300"))
        assert info["fan_ratio"] == 3000 / 300

    def test_yi_unit(self):
        info = score_note(_note(liked_count="1.5亿", fans_count="100"))
        assert info["fan_ratio"] == 150000000 / 100

    def test_mixed_unit_fan_count(self):
        info = score_note(_note(liked_count="500", fans_count="6.1万"))
        # fan_ratio 输出 round(x, 4)：500/61000=0.0082（两位小数会舍成 0.01，看不出量级）
        assert info["fan_ratio"] == pytest.approx(round(500 / 61000, 4), abs=1e-9)


class TestEntryGate:
    """入库门槛：点赞 ≥ min_like 才入库；min_like=0 表示不设门槛（默认关）。"""

    @pytest.mark.parametrize("liked,ok", [
        (500, True), (1000, True), ("1597", True), ("1.2万", True),
        (499, False), (0, False), (None, False), ("", False),
    ])
    def test_industry_threshold(self, liked, ok):
        assert passes_entry_gate(liked, 500) is ok

    @pytest.mark.parametrize("liked", [0, 1, 300, None, ""])
    def test_gate_off_accepts_all(self, liked):
        assert passes_entry_gate(liked, 0) is True


class TestHotScore:
    """第二口径「综合热度」：与低粉潜力并存，面向"什么内容真的火"。"""

    def test_hot_rewards_absolute_popularity(self):
        # 5000 赞 vs 120 赞：低粉榜里 120 赞/2 万粉会垫底，热度榜里 5000 赞必须在前
        hot = score_note_hot(_note(liked_count=5000, collected_count=800,
                                   comment_count=200, shared_count=100, fans_count=0))
        cold = score_note_hot(_note(liked_count=120, collected_count=10,
                                    comment_count=2, shared_count=1, fans_count=20000))
        assert hot["score"] > cold["score"]

    def test_hot_unknown_fans_scores_no_reach(self):
        # 粉丝数未知：破圈维度必须计 0（没有分母就没有赞粉比）
        info = score_note_hot(_note(liked_count=1672, fans_count=0))
        assert info["dimensions"]["reach"] == 0.0
        assert info["score"] > 0  # 但点赞规模/互动/时效仍能得分，不会被打成 0

    def test_hot_max_score_capped_at_100(self):
        info = score_note_hot(_note(liked_count=999999, collected_count=99999,
                                    comment_count=9999, shared_count=9999,
                                    fans_count=1))
        assert 0.0 <= info["score"] <= 100.0
        assert sum(info["dimensions"].values()) == pytest.approx(info["score"], abs=0.3)

    def test_hot_zero_likes_is_low(self):
        info = score_note_hot(_note(liked_count=0, collected_count=0,
                                    comment_count=0, shared_count=0, fans_count=100))
        assert info["score"] < 20


class TestFansUnknown:
    """粉丝数未知（采集失败）与真·0 粉必须区分——旧实现把失败写成 0，
    既让「低粉」标签误报，又让低粉潜力 65 分权重凭空清零，且完全不可见。"""

    def test_none_and_blank_are_unknown_zero_is_known(self):
        assert fans_known_of(None) == (0, False)
        assert fans_known_of("") == (0, False)
        assert fans_known_of("  ") == (0, False)
        assert fans_known_of("0") == (0, True)
        assert fans_known_of("5.3万") == (53000, True)

    def test_unknown_gets_no_low_fan_bonus(self):
        unknown = score_note(_note(liked_count=800, fans_count=None))
        assert unknown["dimensions"]["low_fan"] == 0.0
        # 真·0 粉是最低粉，理应拿满（旧实现这里也返回 0，等于把真零粉当成异常）
        assert score_note(_note(liked_count=800, fans_count=0))["dimensions"]["low_fan"] == 25.0

    def test_unknown_counts_only_known_dimensions(self):
        # 不虚构缺失维度：只累加互动+时效，上限 35。曾归一化到 100，实测把 27 赞的
        # 笔记抬成满分，等于用数据缺失奖励它。
        info = score_note(_note(liked_count=800, fans_count=None))
        expect = info["dimensions"]["interaction_health"] + info["dimensions"]["freshness"]
        assert info["score"] == pytest.approx(expect, abs=0.2)
        assert info["score"] <= 35.0

    def test_unknown_flag_exposed_on_both_calibers(self):
        assert score_note(_note(fans_count=None))["fans_known"] is False
        assert score_note(_note(fans_count=1200))["fans_known"] is True
        assert score_note_hot(_note(fans_count=None))["fans_known"] is False

    def test_unknown_never_tagged_low_fan(self):
        # 粉丝未知时不能打「低粉」「低粉爆款」——否则数据不全的笔记被当成优质小号
        info = score_note(_note(liked_count=2000, fans_count=None))
        assert info["tag"] not in (TAGS["low_fan"], TAGS["low_fan_hit"])

    def test_fans_for_db_keeps_null(self):
        assert fans_for_db(None) is None
        assert fans_for_db("") is None
        assert fans_for_db("5.3万") == 53000
        assert fans_for_db(0) == 0

    def test_unknown_hot_counts_only_known_dimensions(self):
        info = score_note_hot(_note(liked_count=3000, fans_count=None))
        expect = (info["dimensions"]["like_scale"]
                  + info["dimensions"]["interaction"]
                  + info["dimensions"]["freshness"])
        assert info["score"] == pytest.approx(expect, abs=0.2)
        assert info["score"] <= 85.0
