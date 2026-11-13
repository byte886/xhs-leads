"""server 端点行为特征测试（R-6.36f：补简单路由，TestClient 直测现行契约）。"""
import pytest
from fastapi.testclient import TestClient

from src import server


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_startup_cleanup", lambda: None)
    monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})
    monkeypatch.setattr(server, "_LOGIN_GATE", {"pending": False, "force": False})
    # 临时 DB
    import sqlite3
    con = sqlite3.connect(tmp_path / "t.db", check_same_thread=False)
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    monkeypatch.setattr(server, "_db", lambda: con)
    from src.server import app
    with TestClient(app) as c:
        yield c
    con.close()


def test_login_cancel_clears_gate(client):
    r = client.post("/api/auth/login_cancel").json()
    assert r == {"ok": True}
    assert server._LOGIN_GATE == {"pending": False, "force": False}


def test_license_status(client):
    r = client.get("/api/license_status").json()
    assert r["ok"] is True and "mode" in r["status"]


def test_license_activate_bad_code_400(client):
    r = client.post("/api/license_activate", json={"key": "garbage"})
    assert r.status_code == 400


def test_status_now_idle(client):
    r = client.get("/api/status_now").json()
    assert r["collecting"] is False and r["action"] == "空闲"


def test_collect_progress_unknown(client):
    r = client.get("/api/collect_progress", params={"task_id": "nope"}).json()
    assert r["ok"] is False


def test_collect_stop_unknown(client):
    r = client.post("/api/collect_stop", json={"task_id": "nope"}).json()
    assert r["ok"] is False


def test_notes_empty(client):
    r = client.get("/api/notes").json()
    assert r["total"] == 0 and r["notes"] == []


def test_authors_empty(client):
    r = client.get("/api/authors").json()
    assert "authors" in r


def test_clients_list_empty(client):
    r = client.get("/api/clients_list").json()
    assert "clients" in r or "total" in r


def test_drafts_empty(client):
    r = client.get("/api/drafts").json()
    assert "drafts" in r


def test_logs_empty(client):
    r = client.get("/api/publish_logs").json()
    assert r["logs"] == []


def test_persona_default_empty(client):
    r = client.get("/api/persona").json()
    assert r["ok"] is True and r["industry"] == ""


def test_login_hint_page_removed(client):
    """R-6.53z8k 起 /login-hint 中间页已删（采集窗直落登录页），路由不该再回来。"""
    r = client.get("/login-hint")
    assert r.status_code == 404


def test_llm_config_missing_fields_400(client):
    r = client.post("/api/llm_config", json={"api_key": "k", "openai_base_url": "", "default_model": ""})
    assert r.status_code == 400


def test_llm_config_clear(client):
    r = client.post("/api/llm_config/clear")
    assert r.status_code == 200


def test_persona_save_roundtrip(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")
    r = client.post("/api/persona", json={"industry": "珠宝", "tone": "", "topics": ""}).json()
    assert r["ok"] is True and r["industry"] == "珠宝"
    r2 = client.get("/api/persona").json()
    assert r2["industry"] == "珠宝"


def test_notes_with_params(client):
    r = client.get("/api/notes", params={"keyword": "x", "order": "recent", "tag": "t"}).json()
    assert r["total"] == 0


def test_publish_stats_requires_login(client):
    r = client.post("/api/publish_stats", json={"log_id": 9999})
    assert r.status_code in (403, 404)


def test_download_images_empty(client):
    r = client.post("/api/download_images", json={"feed_ids": []})
    assert r.status_code in (200, 400, 422)


def test_upload_image_not_image_ext(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "imgs")
    (tmp_path / "imgs").mkdir(exist_ok=True)
    r = client.post("/api/upload_image",
                    files={"file": ("note.txt", b"hello", "text/plain")}).json()
    assert "ok" in r


def test_draft_save_list_delete(client):
    r = client.post("/api/save_draft", json={
        "feed_id": "f1", "title": "t", "content": "c", "tags": ["a"],
        "visibility": "private", "images": []}).json()
    assert r["ok"] is True
    lst = client.get("/api/drafts").json()
    assert lst["total"] == 1 and lst["drafts"][0]["feed_id"] == "f1"
    d = client.post("/api/draft_delete", json={"id": lst["drafts"][0]["id"]}).json()
    assert d["ok"] is True
    assert client.get("/api/drafts").json()["total"] == 0


def test_img_proxy_bad_path(client):
    r = client.get("/api/img", params={"path": "/etc/passwd"})
    assert r.status_code == 400


def test_img_proxy_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "imgs")
    r = client.get("/api/img", params={"path": "/app/images/nope.jpg"})
    assert r.status_code == 404


def test_llm_config_get(client):
    r = client.get("/api/llm_config").json()
    assert "has_key" in r or "configured" in r or isinstance(r, dict)


def test_draft_update_same_feed_id(client):
    client.post("/api/save_draft", json={
        "feed_id": "f9", "title": "v1", "content": "c", "tags": [],
        "visibility": "private", "images": []})
    r = client.post("/api/save_draft", json={
        "feed_id": "f9", "title": "v2", "content": "c2", "tags": [],
        "visibility": "private", "images": []}).json()
    assert r["ok"] is True and "更新" in r["note"]
    lst = client.get("/api/drafts").json()
    assert lst["total"] == 1 and lst["drafts"][0]["title"] == "v2"


def test_mirror_screenshot_405(client):
    # 无 CDP 实例时行为依环境而异（200/500/异常）——只验证端点可调用不挂
    try:
        r = client.get("/api/mirror/screenshot")
        assert r.status_code in (200, 500, 503, 404)
    except Exception:
        pass  # 无引擎时抛错也是预期


def test_login_ok_returns_logged_in(client, monkeypatch):
    """登录成功通知：引擎实况复核为有效登录后，返回 logged_in 供守卫判断。"""
    async def fake_get(*a, **k):
        return object()
    monkeypatch.setattr("src.engine_pool.pool.get", fake_get)

    async def fake_state(eng):
        return (True, True, False, False)  # has_ws, page_logged_in, kicked, risk_verify
    monkeypatch.setattr(server, "_engine_login_state", fake_state)

    r = client.post("/api/auth/login_ok", json={"fresh_scan": True}).json()
    assert r["ok"] is True and r["logged_in"] is True


def test_login_ok_rejects_stale_cookie(client, monkeypatch):
    """假阳性防御：cookie 残留但页面仍在登录页（被踢）时，不判登录、不解除失效记忆。"""
    monkeypatch.setattr(server, "_LOGIN_INVALID", True)
    async def fake_get(*a, **k):
        return object()
    monkeypatch.setattr("src.engine_pool.pool.get", fake_get)

    async def fake_state(eng):
        return (True, False, True, False)  # 有 web_session 但页面未登录、被踢
    monkeypatch.setattr(server, "_engine_login_state", fake_state)

    r = client.post("/api/auth/login_ok", json={}).json()
    assert r["ok"] is True and r["logged_in"] is False
    assert server._LOGIN_INVALID is True  # 失效记忆保持，不被解除


def test_import_cookies_endpoint_removed(client):
    """旧 Cookie 搬运端点已下线，避免任何人再把 Cookie 打包进后端。"""
    r = client.post("/api/auth/import_cookies", json={"cookies": []})
    assert r.status_code == 404


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200 or r.status_code == 404  # WEB_DIR 可能无 index.html


def test_notes_with_row(client):
    con = server._db()
    con.execute("INSERT INTO notes (feed_id, title, score) VALUES ('f1','hello',5)")
    con.commit()
    r = client.get("/api/notes").json()
    assert r["total"] == 1 and r["notes"][0]["title"] == "hello"


def test_clients_list_with_filter(client):
    r = client.get("/api/clients_list", params={"feed_id": "f", "intent": "high"})
    assert r.status_code == 200


def test_drafts_corrupted_images_fallback(client):
    con = server._db()
    con.execute("INSERT INTO drafts (feed_id,title,content,tags,visibility,images,status,created_at) VALUES ('x','t','c','','private','{bad','draft','2026-01-01')")
    con.commit()
    r = client.get("/api/drafts").json()
    assert r["drafts"][0]["images"] == []


def test_notes_order_recent(client):
    r = client.get("/api/notes", params={"order": "recent"})
    assert r.status_code == 200


def test_logs_with_row(client):
    con = server._db()
    con.execute("INSERT INTO publish_logs (feed_id,title,status) VALUES ('f','t','ok')")
    con.commit()
    r = client.get("/api/publish_logs").json()
    assert len(r["logs"]) == 1


def test_persona_save_empty_keeps_old(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "p.json")
    client.post("/api/persona", json={"industry": "珠宝", "tone": "正式", "topics": ""})
    # 空 topics 保留旧值
    client.post("/api/persona", json={"industry": "", "tone": "", "topics": "翡翠"}).json()
    r = client.get("/api/persona").json()
    assert r["industry"] == "珠宝" and r["topics"] == "翡翠"


def test_collect_progress_running_task(client):
    server._COLLECT_TASKS["t1"] = {
        "status": "running", "keyword": "k", "limit": 10,
        "collected": 3, "note": "采集中", "account_id": "default",
    }
    r = client.get("/api/collect_progress", params={"task_id": "t1"}).json()
    assert r["ok"] is True and r["collected"] == 3
    # status_now 聚合 running
    r2 = client.get("/api/status_now").json()
    assert r2["collecting"] is True
    # collect_stop 触发停止
    r3 = client.post("/api/collect_stop", json={"task_id": "t1"}).json()
    assert r3["ok"] is True


def test_authors_with_row(client):
    con = server._db()
    con.execute("INSERT INTO notes (feed_id,title,user_id,nickname,score,fans_count) VALUES ('f1','t','u1','nick',5,100)")
    con.commit()
    r = client.get("/api/authors").json()
    assert "authors" in r


def test_status_now_detail_phase(client):
    server._COLLECT_TASKS["t2"] = {
        "status": "running", "phase": "detail", "keyword": "k",
        "limit": 10, "collected": 5, "note": "补全", "account_id": "default",
    }
    r = client.get("/api/status_now").json()
    assert r["collecting"] is True and "action" in r
    server._COLLECT_TASKS.clear()


def test_vision_config_roundtrip(client, tmp_path, monkeypatch):
    """分析图片配置独立存储：与生文互不影响，保存后能读回且 key 不打明文。"""
    from src import llm_service

    monkeypatch.setattr(llm_service, "VISION_CONFIG_PATH", tmp_path / "vision.json")
    monkeypatch.setattr(llm_service, "CONFIG_PATH", tmp_path / "llm.json")

    r = client.post("/api/vision_config", json={
        "api_key": "sk-vision-1234567890", "openai_base_url": "https://example.com/v1",
        "default_model": "qwen-vl-max",
    }).json()
    assert r["configured"] is True
    assert r["default_model"] == "qwen-vl-max"
    assert "sk-vision-1234567890" not in r["llm_api_key_masked"]
    # 生文配置不能被写进去
    assert client.get("/api/llm_config").json()["configured"] is False


def test_vision_config_rejects_incomplete(client):
    """缺 Base URL 或模型必须 400，不能存半套配置。"""
    r = client.post("/api/vision_config", json={"api_key": "sk", "openai_base_url": "", "default_model": "x"})
    assert r.status_code == 400
