"""server 路由层冒烟 + 落库/保存等函数测试（monkeypatch 掉浏览器依赖）。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import server
from src.server import _persist_notes, _save_cookies_file


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(server, "_startup_cleanup", lambda: None)  # 跳过 Chrome tab 清理
    from src.server import app
    with TestClient(app) as c:
        yield c


class TestApiSmoke:
    def test_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"<!DOCTYPE html" in r.content or b"<div" in r.content

    def test_login_status(self, client, monkeypatch):
        monkeypatch.setattr(server, "_read_cookie_status", lambda p: (True, "abc", True))
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})
        r = client.get("/api/login_status")
        assert r.json()["logged_in"] is True

    def test_cookie_info_empty(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "BASE", tmp_path)
        monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
        r = client.get("/api/cookie_info")
        assert r.json()["ok"] is True and r.json()["count"] == 0

    def test_cleanup_api(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "img")
        (tmp_path / "img").mkdir(exist_ok=True)
        r = client.post("/api/cleanup_images")
        assert r.json() == {"ok": True, "removed": 0}

    def test_unknown_route_404(self, client):
        assert client.get("/api/nope").status_code == 404


class TestPersistNotes:
    def test_replace_batch(self, tmp_db, monkeypatch):
        monkeypatch.setattr(server, "_db", lambda: tmp_db)
        n1 = {"feed_id": "a1", "liked_count": 1000, "fans_count": 100,
              "collected_count": 0, "comment_count": 0, "shared_count": 0, "publish_time": None}
        n2 = {"feed_id": "a2", "liked_count": 100, "fans_count": 2000,
              "collected_count": 0, "comment_count": 0, "shared_count": 0, "publish_time": None}
        added, total, top = _persist_notes("翡翠", [n1, n2])
        assert added == 2 and total == 2 and len(top) == 2
        assert top[0]["feed_id"] == "a1"  # 高分在前


class TestSaveCookiesFile:
    def test_writes_and_backup(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "BASE", tmp_path)
        monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
        _save_cookies_file([{"name": "web_session", "value": "v1"}])
        p = tmp_path / "data" / "cookies.json"
        assert p.exists()
        _save_cookies_file([{"name": "a1", "value": "v2"}])
        assert (tmp_path / "data" / "cookies.json.bak").exists()
        data = json.loads(p.read_text(encoding="utf8"))
        assert data["cookies"][0]["name"] == "a1"


class TestImgProxy:
    def test_path_guard(self, client):
        assert client.get("/api/img").status_code == 400
        assert client.get("/api/img?path=/etc/passwd").status_code == 400

    def test_serves_image(self, client, monkeypatch, tmp_path):
        d = tmp_path / "img"
        d.mkdir()
        (d / "x.jpg").write_bytes(b"\xff\xd8ff")
        monkeypatch.setattr(server, "IMAGES_DIR", d)
        r = client.get("/api/img?path=" + "/app/images/x.jpg")
        assert r.status_code == 200
        assert r.content == b"\xff\xd8ff"

    def test_missing_image(self, client, monkeypatch, tmp_path):
        d = tmp_path / "img"
        d.mkdir()
        monkeypatch.setattr(server, "IMAGES_DIR", d)
        assert client.get("/api/img?path=/app/images/none.jpg").status_code == 404
