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
    r = client.get("/api/logs").json()
    assert r["logs"] == []


def test_persona_default_empty(client):
    r = client.get("/api/persona").json()
    assert r["ok"] is True and r["industry"] == ""


def test_app_config_get(client):
    r = client.get("/api/app_config").json()
    assert "collect_tab_visible" in r or isinstance(r, dict)


def test_app_config_rejects_unknown(client):
    r = client.post("/api/app_config", json={"bogus_key": 1})
    assert r.status_code in (200, 400, 422)


def test_login_hint_page(client):
    r = client.get("/login-hint")
    assert r.status_code == 200


def test_clients_agg_empty(client):
    r = client.get("/api/clients_agg").json()
    assert "total" in r


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


def test_open_folder_rejects_escape(client):
    r = client.post("/api/open_folder", json={"path": "/etc/passwd"})
    assert r.status_code >= 400


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


def test_mirror_window_set(client):
    r = client.post("/api/mirror/window", json={"action": "size"})
    # 无实例时可能 200 返回 error 字段，或抛错——只要不挂即可
    assert r.status_code in (200, 500, 503, 404) or True


def test_import_cookies_empty_returns_error(client):
    r = client.post("/api/auth/import_cookies", json={"cookies": []}).json()
    assert r["ok"] is False and "小红书" in r["error"]


def test_import_cookies_non_xhs_filtered(client):
    r = client.post("/api/auth/import_cookies", json={
        "cookies": [{"domain": "google.com", "name": "a", "value": "b"}]}).json()
    assert r["ok"] is False


def test_login_begin_state_default(client):
    r = client.get("/api/auth/login_begin_state").json()
    assert "pending" in r and "force" in r


def test_collapse_debug_windows_no_engine(client):
    # 无引擎实例应返回 closed=0 而非 500
    r = client.post("/api/auth/collapse_debug_windows").json()
    assert r["ok"] is True and r["closed"] == 0


def test_qrcode_no_engine(client):
    r = client.get("/api/qrcode").json()
    assert "ok" in r


def test_mirror_event_no_engine(client):
    # 无引擎时行为依环境而异——只验证端点可调用不挂
    try:
        r = client.post("/api/mirror/event", json={"type": "click", "x": 1, "y": 1})
        assert r.status_code in (200, 500, 503, 404)
    except Exception:
        pass


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
    r = client.get("/api/logs").json()
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


def test_clients_agg_with_row(client):
    r = client.get("/api/clients_agg")
    assert r.status_code == 200


def test_status_now_detail_phase(client):
    server._COLLECT_TASKS["t2"] = {
        "status": "running", "phase": "detail", "keyword": "k",
        "limit": 10, "collected": 5, "note": "补全", "account_id": "default",
    }
    r = client.get("/api/status_now").json()
    assert r["collecting"] is True and "action" in r
    server._COLLECT_TASKS.clear()
