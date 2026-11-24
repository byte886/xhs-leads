"""客户库跨会话累积：清本批不清客户 / 显式清空接口 / 同人去重合并。

口径（用户 2026-11-24 拍板）：客户采集要跨天、跨会话累积；
若累积有阻塞才退化成「采集前清空」。本文件锁定累积路径的四个不变量。
"""
import sqlite3

from fastapi.testclient import TestClient

from src import server


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "acc.db")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "images")
    (tmp_path / "images").mkdir(exist_ok=True)
    # 不走 lifespan：本文件只验证数据口径，不需要启动钩子里的引擎清理
    return TestClient(server.app)


def _rows(tmp_path, sql):
    con = sqlite3.connect(tmp_path / "acc.db")
    rows = con.execute(sql).fetchall()
    con.close()
    return rows


def test_collect_clear_keeps_clients(monkeypatch, tmp_path):
    """点「开始采集」前的清库只清本批，客户线索必须留下。"""
    c = _client(monkeypatch, tmp_path)
    con = sqlite3.connect(tmp_path / "acc.db")
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    con.execute("INSERT INTO notes (feed_id, title) VALUES ('n1', '笔记')")
    con.execute("INSERT INTO clients (uid, feed_id, nickname, comment) VALUES ('u1','f1','老王','怎么卖')")
    con.commit()
    con.close()

    r = c.post("/api/collect_clear")
    assert r.status_code == 200
    assert _rows(tmp_path, "SELECT COUNT(*) FROM notes")[0][0] == 0
    assert _rows(tmp_path, "SELECT COUNT(*) FROM clients")[0][0] == 1


def test_clients_clear_all(monkeypatch, tmp_path):
    """不带 feed_id：显式清空全库。"""
    c = _client(monkeypatch, tmp_path)
    con = sqlite3.connect(tmp_path / "acc.db")
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    con.execute("INSERT INTO clients (uid, feed_id, nickname, comment) VALUES ('u1','f1','甲','多少钱')")
    con.execute("INSERT INTO clients (uid, feed_id, nickname, comment) VALUES ('u2','f2','乙','求链接')")
    con.commit()
    con.close()

    r = c.post("/api/clients_clear", json={})
    assert r.status_code == 200
    assert r.json()["removed"] == 2
    assert _rows(tmp_path, "SELECT COUNT(*) FROM clients")[0][0] == 0


def test_clients_clear_one_note(monkeypatch, tmp_path):
    """带 feed_id：只清该篇，别的笔记的客户不受影响。"""
    c = _client(monkeypatch, tmp_path)
    con = sqlite3.connect(tmp_path / "acc.db")
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    con.execute("INSERT INTO clients (uid, feed_id, nickname, comment) VALUES ('u1','f1','甲','多少钱')")
    con.execute("INSERT INTO clients (uid, feed_id, nickname, comment) VALUES ('u2','f2','乙','求链接')")
    con.commit()
    con.close()

    r = c.post("/api/clients_clear", json={"feed_id": "f1"})
    assert r.json()["removed"] == 1
    remaining = _rows(tmp_path, "SELECT uid FROM clients")
    assert remaining == [("u2",)]


def test_accumulate_across_batches(monkeypatch, tmp_path):
    """同人同一篇二次采集：评论合并、人数不重复计；不带 feed_id 能看全库。"""
    c = _client(monkeypatch, tmp_path)
    con = sqlite3.connect(tmp_path / "acc.db")
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    from src import clients_flow

    clients_flow.ingest(con, "f1", "author", [{"uid": "u1", "nickname": "甲", "comment": "多少钱"}], "2026-11-23T10:00:00")
    clients_flow.ingest(con, "f1", "author", [{"uid": "u1", "nickname": "甲", "comment": "有证书吗"}], "2026-11-24T10:00:00")
    clients_flow.ingest(con, "f2", "author", [{"uid": "u2", "nickname": "乙", "comment": "求链接"}], "2026-11-24T10:05:00")
    con.commit()
    con.close()

    g = c.get("/api/clients_list").json()
    assert g["total"] == 2  # 按人去重：u1 出现两次仍只算一人
    by_uid = {x["uid"]: x for x in g["clients"]}
    assert by_uid["u1"]["comment_count"] == 2
    assert by_uid["u2"]["note_count"] == 1

    # 单篇视图仍是子集
    one = c.get("/api/clients_list", params={"feed_id": "f1"}).json()
    assert one["total"] == 1
