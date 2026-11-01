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
        # R 批 v1.5：登录态 = 引擎 CDP web_session 存在 且 页面登录（xhsIsLoggedIn），
        # 两条件任一不满足即未登录（web_session 存在但会话失效=假阳性场景，实测见 R-6）
        # R-6.8：_engine_login_state 返回三元组 (web_session?, 页面登录?, 被踢?)；被踢 → 判失效
        async def fake_get(*a, **k):
            return object()  # 假引擎实例：仅用于传入 _engine_login_state

        monkeypatch.setattr("src.engine_pool.pool.get", fake_get)
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})

        async def state_ok(eng):
            return True, True, False

        async def state_stale(eng):
            return True, False, False  # stale cookie：web_session 有但页面未登录

        async def state_kicked(eng):
            return True, True, True  # R-6.8：cookie 残留但平台已踢（登录超限/请重新登录）

        monkeypatch.setattr(server, "_engine_login_state", state_ok)
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})  # 防跨用例缓存污染
        assert client.get("/api/login_status").json()["logged_in"] is True

        monkeypatch.setattr(server, "_engine_login_state", state_stale)
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})  # 重置 30s 缓存
        assert client.get("/api/login_status").json()["logged_in"] is False

        monkeypatch.setattr(server, "_engine_login_state", state_kicked)
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})  # 重置 30s 缓存
        r = client.get("/api/login_status").json()
        assert r["logged_in"] is False and r["status"] == "kicked" and r["kicked"] is True

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
