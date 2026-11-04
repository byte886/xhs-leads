"""collect_pipeline 纯函数特征测试（R-6.36b：解析/合并现行行为固化，无浏览器）。"""
from src import collect_pipeline as cp


class TestParseLikes:
    def test_plain(self):
        assert cp._parse_likes("151") == 151

    def test_comma(self):
        assert cp._parse_likes("1,234") == 1234

    def test_wan(self):
        assert cp._parse_likes("1.2万") == 12000

    def test_qian(self):
        assert cp._parse_likes("3千") == 3000

    def test_999plus(self):
        assert cp._parse_likes("999+") == 999

    def test_empty(self):
        assert cp._parse_likes("") == 0 and cp._parse_likes(None) == 0

    def test_garbage(self):
        assert cp._parse_likes("abc") == 0


class TestParseFans:
    def test_wan(self):
        assert cp._parse_fans("1.2万") == 12000

    def test_w(self):
        assert cp._parse_fans("12.3w") == 123000

    def test_plain(self):
        assert cp._parse_fans("3000") == 3000

    def test_empty(self):
        assert cp._parse_fans("") == 0


class TestMergeDetailCore:
    def test_title_desc_type_override(self):
        note = {"title": "old"}
        cp._merge_detail_core(note, {"title": "new", "desc": "d", "type": "video"})
        assert note["title"] == "new" and note["note_type"] == "video"

    def test_fallback_title_from_desc(self):
        note = {}
        cp._merge_detail_core(note, {"desc": "#话题  这是正文内容前二十字以下"})
        # 现行行为：lstrip('#') 后取前 20 字（话题前缀保留）
        assert note["title"] == "话题  这是正文内容前二十字以下"[:20]

    def test_author_fields(self):
        note = {}
        cp._merge_detail_core(note, {"user_id": "u1", "author_nick": "n1", "imgs": ["a.jpg"]})
        assert note["user_id"] == "u1" and note["nickname"] == "n1"
        assert note["detail_imgs"] == ["a.jpg"]


class TestMergeInteractions:
    def test_copies_present_keys(self):
        note = {}
        cp._merge_detail_interactions(note, {"liked_count": 10, "views": 99})
        assert note["liked_count"] == 10 and note["views"] == 99

    def test_ms_to_sec(self):
        note = {}
        cp._merge_detail_interactions(note, {"publish_time_ms": 1700000000000})
        assert note["publish_time"] == 1700000000


class TestSetRhythm:
    """R-6.47 全局请求间隔：会话节奏设置与安全范围 clamp。"""

    def test_default_1000_equals_historical(self):
        cp.set_rhythm(1000)
        assert cp._RHYTHM == (1.0, 1.8)  # 默认与写死时代完全一致

    def test_slow_down(self):
        cp.set_rhythm(3000)
        assert cp._RHYTHM == (3.0, 3.8)

    def test_clamp_lower_bound(self):
        cp.set_rhythm(500)  # 低于 1s：尊重「勿再调快」下限保护
        assert cp._RHYTHM == (1.0, 1.8)

    def test_clamp_upper_bound(self):
        cp.set_rhythm(20000)  # 高于 10s：防呆上限
        assert cp._RHYTHM == (10.0, 10.8)

    def test_reset_to_default(self):
        cp.set_rhythm(5000)
        cp.set_rhythm(1000)
        assert cp._RHYTHM == (1.0, 1.8)
