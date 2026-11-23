"""server 纯逻辑单测：图片名/文本解析/登录态判定/落库/图片清理。"""

from src import server
from src.server import (
    _cleanup_orphan_images, _img_name, _rank_and_insert,
)



class TestImgName:
    def test_hash_replaced(self):
        assert _img_name("abc#def") == "abc_def_cover.jpg"

    def test_suffix(self):
        assert _img_name("id123", suffix="p1") == "id123_p1.jpg"


class TestRankAndInsert:
    def test_inserts_scored(self, tmp_db):
        n = {"feed_id": "f1", "liked_count": 1000, "fans_count": 100, "publish_time": None}
        assert _rank_and_insert(tmp_db, n, "翡翠", "2026-01-01T00:00:00") is True
        row = tmp_db.execute("SELECT * FROM notes WHERE feed_id='f1'").fetchone()
        assert row["keyword"] == "翡翠"
        assert row["score"] > 0

    def test_bad_note_no_crash(self, tmp_db):
        # 空 dict：评分不抛、插入容忍（TEXT 主键允许 NULL）；函数不抛异常即可
        _rank_and_insert(tmp_db, {}, "k", "t")


class TestCleanupOrphanImages:
    def test_keeps_referenced_removes_rest(self, tmp_images, monkeypatch, tmp_path):
        (tmp_images / "ref_cover.jpg").write_bytes(b"x")
        (tmp_images / "orphan_cover.jpg").write_bytes(b"x")
        (tmp_images / "ref_p1.jpg").write_bytes(b"x")

        class FakeRows:
            def fetchall(self):
                return [("ref",)]

        class FakeCon:
            def execute(self, *a):
                return FakeRows()
            def close(self):
                pass
        monkeypatch.setattr(server, "_db", lambda: FakeCon())
        assert _cleanup_orphan_images() == 1
        assert (tmp_images / "ref_cover.jpg").exists()
        assert (tmp_images / "ref_p1.jpg").exists()
        assert not (tmp_images / "orphan_cover.jpg").exists()
