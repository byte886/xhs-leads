"""server 登录链特征测试（R-6.36b：login_status 四元组/import_cookies/login_begin 现行契约固化）。

全部经 monkeypatch 切断真实引擎/CDP；只描述现行行为。
"""
import pytest
from fastapi.testclient import TestClient

from src import server


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(server, "_startup_cleanup", lambda: None)
    monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})
    monkeypatch.setattr(server, "_LOGIN_GATE", {"pending": False, "force": False})
    from src.server import app
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------- login_status 四元组

def _fake_engine(monkeypatch, state_tuple):
    async def fake_get(*a, **k):
        return object()
    monkeypatch.setattr("src.engine_pool.pool.get", fake_get)

    async def fake_state(eng):
        return state_tuple
    monkeypatch.setattr(server, "_engine_login_state", fake_state)


def test_login_status_risk_verify_not_logged_in(client, monkeypatch):
    """R-6.22e：风控需人工验证 → logged_in=False 且 risk_verify=True。"""
    _fake_engine(monkeypatch, (True, True, False, True))
    r = client.get("/api/login_status").json()
    assert r["logged_in"] is False and r["risk_verify"] is True


def test_login_status_not_logged_in(client, monkeypatch):
    _fake_engine(monkeypatch, (False, False, False, False))
    r = client.get("/api/login_status").json()
    assert r["logged_in"] is False and r["status"] == "not_logged_in"


# ------------------------------------------------------- login_begin gate

def test_login_begin_force_sets_gate(client, monkeypatch):
    async def collapse():
        return 0
    fake_eng = type("Eng", (), {"collapse_debug_windows": collapse})()

    async def fake_get(*a, **k):
        return fake_eng
    monkeypatch.setattr("src.engine_pool.pool.get", fake_get)
    r = client.post("/api/auth/login_begin", json={"force": True}).json()
    assert r["ok"] is True and r["force"] is True
    st = client.get("/api/auth/login_begin_state").json()
    assert st["pending"] is True and st["force"] is True


def test_login_begin_state_default_not_pending(client):
    r = client.get("/api/auth/login_begin_state").json()
    assert r == {"pending": False, "force": False}


# ------------------------------------------------------- import_cookies 分支

class _FakeEng:
    def __init__(self, inject_ok=True, pages=()):
        self._inject_ok = inject_ok
        self._pages = list(pages)
    async def inject_cookies(self, cookies):
        return self._inject_ok
    async def context(self):
        e = self
        class Ctx:
            pages = e._pages
        return Ctx()


def _monkeypatch_engine(monkeypatch, eng):
    async def fake_get(*a, **k):
        return eng
    monkeypatch.setattr("src.engine_pool.pool.get", fake_get)


def test_import_cookies_no_xhs_domain(client, monkeypatch):
    """过滤后无小红书域 cookie → ok:False 不注入。"""
    eng = _FakeEng()
    _monkeypatch_engine(monkeypatch, eng)
    r = client.post("/api/auth/import_cookies",
                     json={"cookies": [{"name": "a", "value": "b", "domain": "example.com"}]})
    assert r.json()["ok"] is False and "未捕获到" in r.json()["error"]


def test_import_cookies_inject_fails(client, monkeypatch):
    _monkeypatch_engine(monkeypatch, _FakeEng(inject_ok=False))
    r = client.post("/api/auth/import_cookies",
                     json={"cookies": [{"name": "web_session", "value": "x", "domain": ".xiaohongshu.com"}]})
    assert r.json()["ok"] is False and "注入引擎失败" in r.json()["error"]


def test_import_cookies_logged_in_success(client, monkeypatch):
    """注入+判定通过 → logged_in 且 gate 清。"""
    monkeypatch.setattr(server, "_LOGIN_GATE", {"pending": True, "force": True})
    _monkeypatch_engine(monkeypatch, _FakeEng(pages=[]))

    async def state_ok(eng):
        return True, True, False, False
    monkeypatch.setattr(server, "_engine_login_state", state_ok)
    r = client.post("/api/auth/import_cookies",
                     json={"cookies": [{"name": "web_session", "value": "x", "domain": ".xiaohongshu.com"}]})
    j = r.json()
    assert j["ok"] is True and j["logged_in"] is True and j["status"] == "logged_in"
    assert server._LOGIN_GATE["pending"] is False
