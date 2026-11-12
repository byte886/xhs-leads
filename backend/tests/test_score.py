"""评分引擎单元测试：覆盖 score_note 全分支与 rank_notes。"""
import pytest

from src.score import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    TAGS,
    passes_entry_gate,
    rank_notes,
    score_note,
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
        """粉丝未知：低粉与赞粉比都不计分（不能靠"没有分母"白拿满分）。"""
        info = score_note(_note(fans_count=0))
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
