"""采集管道纯逻辑单测：数字解析、建库、CLI 落库。"""
import datetime
import sqlite3
from pathlib import Path

from src import collect_pipeline as cp


class TestParseLikes:
    def test_plain(self):
        assert cp._parse_likes("1234") == 1234
        assert cp._parse_likes("1.2万") == 12000
        assert cp._parse_likes("3千") == 3000
        assert cp._parse_likes("") == 0
        assert cp._parse_likes("abc") == 0


class TestParseFans:
    def test_units(self):
        assert cp._parse_fans("1.2万") == 12000
        assert cp._parse_fans("12.3w") == 123000
        assert cp._parse_fans("3000") == 3000
        assert cp._parse_fans("1,200") == 1200

    def test_garbage(self):
        assert cp._parse_fans("") == 0
        assert cp._parse_fans(None) == 0
        assert cp._parse_fans("abc") == 0


class TestInitDb:
    def test_creates_tables(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        con = cp.init_db(d / "notes.db")
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert "notes" in tables
        finally:
            con.close()

    def test_existing_db_reused(self, tmp_path):
        p = tmp_path / "n.db"
        cp.init_db(p).close()
        con2 = cp.init_db(p)
        con2.close()  # 不抛即复用成功


class TestUpsertNote:
    def test_insert_and_replace(self, tmp_path):
        con = cp.init_db(tmp_path / "n.db")
        n = {"feed_id": "f1", "score_info": {"score": 88.0, "tag": "低粉", "fan_ratio": 3.0},
             "liked_count": 1000, "fans_count": 100, "collected_count": 0, "comment_count": 0,
             "shared_count": 0, "publish_time": 1}
        cp._upsert_note(con, n, "翡翠", "2026-01-01T00:00:00")
        row = con.execute("SELECT feed_id, keyword, score FROM notes WHERE feed_id='f1'").fetchone()
        assert row[1] == "翡翠" and row[2] == 88.0
        n["score_info"]["score"] = 90.0
        cp._upsert_note(con, n, "翡翠", "2026-01-01T00:00:01")
        assert con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
        con.close()
