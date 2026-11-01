"""server 路由层批量补测：覆盖 qrcode/search/collect/notes/download/detail/clone/clients/
cookie 全链路 + publish 护栏 + 图片代理（monkeypatch 浏览器依赖，纯逻辑验证）。"""
import asyncio
import datetime
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import server
from src.server import _rank_and_insert, _startup_cleanup, _verify_cookies_or_raise

# fixture 会把 _read_cookie_status 替换为恒真 lambda；需要测真实登录态判定时还原
_REAL_READ_COOKIE_STATUS = server._read_cookie_status


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from src import browser_client

    async def fake_cleanup_tabs():
        return None

    # 真实登录链路（引擎 CDP+页面深度校验）由 test_real_login.py 专门覆盖；
    # 本模块纯逻辑验证一律 mock 为已登录，不依赖真实浏览器/引擎（pytest 进程无登录态、
    # 引擎 profile 被真实验证实例占用会 403 误报 / unknown 误放——见 R-5 回归修复记录）。
    async def fake_real_login():
        return {"ok": True, "code": "test_mock", "note": "fixture mock 已登录"}

    async def fake_login_status():
        return {"status": "logged_in", "logged_in": True, "nickname": "", "raw": "fixture mock 已登录"}

    monkeypatch.setattr(server, "_require_real_login", fake_real_login)
    monkeypatch.setattr(server, "login_status", fake_login_status)
    monkeypatch.setattr(browser_client, "cleanup_tabs", fake_cleanup_tabs)
    monkeypatch.setattr(server, "_cleanup_orphan_images", lambda: 0)
    monkeypatch.setattr(server, "_startup_cleanup", lambda: None)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    (tmp_path / "images").mkdir(exist_ok=True)
    monkeypatch.setattr(server, "BASE", tmp_path)
    monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})
    from src.server import app
    with TestClient(app) as c:
        yield c
    server._COLLECT_TASKS.clear()


def _seed_notes(tmp_path, rows=None):
    con = sqlite3.connect(tmp_path / "test.db")
    con.row_factory = sqlite3.Row
    server._init_tables(con)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for r in rows or [("a1", "翡翠手镯", "翡翠"), ("b2", "和田玉", "玉")]:
        _rank_and_insert(con, {"feed_id": r[0], "title": r[1], "liked_count": "100",
                               "fans_count": "1000"}, r[2], now)
    con.commit()
    con.close()


def _seed_publish_log(con, status, raw="", created_at=None):
    ts = created_at or datetime.datetime.now().isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at, raw) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("", "t", "c", "", "仅自己可见", status, ts, raw),
    )
    con.commit()


class TestStartupCleanup:
    def test_startup_ok(self, monkeypatch):
        from src import browser_client

        async def fake_cleanup():
            return None

        monkeypatch.setattr(browser_client, "cleanup_tabs", fake_cleanup)
        asyncio.run(_startup_cleanup())  # 不抛即可

    def test_startup_errors_silent(self, monkeypatch):
        from src import browser_client

        async def boom():
            raise RuntimeError("chrome 不可用")

        monkeypatch.setattr(browser_client, "cleanup_tabs", boom)
        monkeypatch.setattr(server, "_cleanup_orphan_images", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        asyncio.run(_startup_cleanup())  # 双异常均吞掉


class TestQrcode:
    def test_qrcode_ok(self, client, monkeypatch):
        # R 批 v1.4：登录引导改走 app 内镜像（引擎独立 profile），不再拉起外部 Chrome
        from src import mirror

        async def fake_open_login(account="default"):
            return {"ok": True}

        monkeypatch.setattr(mirror, "open_login", fake_open_login)
        r = client.get("/api/qrcode")
        assert r.json()["ok"] is True and "登录小窗" in r.json()["guide"]

    def test_qrcode_fail(self, client, monkeypatch):
        from src import mirror

        async def boom(account="default"):
            raise RuntimeError("引擎未启动")

        monkeypatch.setattr(mirror, "open_login", boom)
        r = client.get("/api/qrcode")
        assert r.json()["ok"] is False and "引擎未启动" in r.json()["error"]


class TestSearch:
    def test_search_ok(self, client, monkeypatch):
        async def fake_search(*a, **k):
            return {"items": [{"feed_id": "x1", "title": "t", "liked_count": "100"}]}

        monkeypatch.setattr(server, "search_via_browser", fake_search)
        r = client.post("/api/search", json={"keyword": "翡翠", "limit": 20})
        assert r.json()["added"] == 1 and r.json()["source"].startswith("关键词搜索")

    def test_search_exception(self, client, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("浏览器未连接")

        monkeypatch.setattr(server, "search_via_browser", boom)
        r = client.post("/api/search", json={"keyword": "x"})
        assert r.json()["added"] == 0 and "浏览器未连接" in r.json()["note"]

    def test_search_empty(self, client, monkeypatch):
        async def empty(*a, **k):
            return {"items": []}

        monkeypatch.setattr(server, "search_via_browser", empty)
        r = client.post("/api/search", json={"keyword": "x"})
        assert r.json()["added"] == 0 and "未返回结果" in r.json()["note"]

    def test_search_not_logged_in(self, client, monkeypatch):
        # R 批 v1.5：登录态判定改引擎真实链路（login_status），未登录 → 403 引导登录小窗
        async def fake_not_logged_in():
            return {"status": "not_logged_in", "logged_in": False, "nickname": "", "raw": "engine-cdp 未登录"}

        monkeypatch.setattr(server, "login_status", fake_not_logged_in)
        r = client.post("/api/search", json={"keyword": "x"})
        assert r.status_code == 403 and "登录态已失效" in r.json()["detail"]


class TestCollect:
    def _run_collect(self, client, monkeypatch, result):
        async def fake_search(*a, **k):
            return result

        async def fake_enrich(items, **k):
            return items  # 纯逻辑测试：跳过真实浏览器详情补全（否则会调 CDP/网络挂起）

        monkeypatch.setattr(server, "search_via_browser", fake_search)
        monkeypatch.setattr(server, "_enrich_notes", fake_enrich)
        r = client.post("/api/collect_start", json={"keyword": "k", "limit": 3})
        tid = r.json()["task_id"]
        pr = {"status": "running"}
        for _ in range(100):
            pr = client.get(f"/api/collect_progress?task_id={tid}").json()
            if pr["status"] != "running":
                break
            time.sleep(0.02)
        return tid, pr

    def test_collect_done(self, client, monkeypatch):
        _, pr = self._run_collect(client, monkeypatch, {"items": [{"feed_id": "a", "title": "t", "liked_count": "5"}], "stopped": False})
        assert pr["status"] == "done"

    def test_collect_stopped(self, client, monkeypatch):
        _, pr = self._run_collect(client, monkeypatch, {"items": [], "stopped": True})
        assert pr["status"] == "stopped"

    def test_collect_error(self, client, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("采集失败")

        monkeypatch.setattr(server, "search_via_browser", boom)
        r = client.post("/api/collect_start", json={"keyword": "k", "limit": 3})
        tid = r.json()["task_id"]
        pr = {"status": "running"}
        for _ in range(100):
            pr = client.get(f"/api/collect_progress?task_id={tid}").json()
            if pr["status"] != "running":
                break
            time.sleep(0.02)
        assert pr["status"] == "error" and "采集失败" in pr["note"]

    def test_collect_filters_passthrough(self, client, monkeypatch):
        """前端 5 个筛选参数（排序/类型/时间/范围/距离）必须透传到 search_via_browser.filters。

        对应 CollectPanel.vue 的 sort/noteType/timeRange/scope/distance 5 个 select 与
        browser_client._apply_filters 的 5 类筛选（跳过默认值逻辑）。
        """
        got = {}

        async def fake_search(*a, **k):
            got["filters"] = k.get("filters")
            return {"items": [{"feed_id": "f1", "title": "t"}], "stopped": False}

        async def fake_enrich(items, **k):
            return items  # 跳过真实浏览器详情补全（同 _run_collect）

        monkeypatch.setattr(server, "search_via_browser", fake_search)
        monkeypatch.setattr(server, "_enrich_notes", fake_enrich)
        r = client.post("/api/collect_start", json={
            "keyword": "翡翠", "limit": 3,
            "sort": "最新", "note_type": "视频", "time_range": "一年内",
            "scope": "未看过", "distance": "同城",
        })
        tid = r.json()["task_id"]
        for _ in range(100):
            pr = client.get(f"/api/collect_progress?task_id={tid}").json()
            if pr["status"] != "running":
                break
            time.sleep(0.02)
        assert pr["status"] == "done"
        # 5 个筛选一个不少、原样透传（中文选项，与 _apply_filters 消费格式一致）
        assert got["filters"] == {
            "sort": "最新", "note_type": "视频", "time_range": "一年内",
            "scope": "未看过", "distance": "同城",
        }

    def test_collect_filters_defaults_skipped(self, client, monkeypatch):
        """默认值（综合/全部/不限）必须被 _apply_filters 跳过——不产生多余的筛选点击。

        对应 browser_client._apply_filters：val 在（综合/全部/不限/空）时整项不应用。
        """
        got = {}

        async def fake_search(*a, **k):
            got["filters"] = k.get("filters")
            return {"items": [], "stopped": False}

        async def fake_enrich(items, **k):
            return items

        monkeypatch.setattr(server, "search_via_browser", fake_search)
        monkeypatch.setattr(server, "_enrich_notes", fake_enrich)
        r = client.post("/api/collect_start", json={
            "keyword": "k", "limit": 3,
            "sort": "综合", "note_type": "全部", "time_range": "",
            "scope": "不限", "distance": "",
        })
        tid = r.json()["task_id"]
        for _ in range(100):
            pr = client.get(f"/api/collect_progress?task_id={tid}").json()
            if pr["status"] != "running":
                break
            time.sleep(0.02)
        assert pr["status"] == "done"
        # 全默认：透传后 _apply_filters 应全部跳过（wanted 为空）——这里只验证透传本身，
        # 跳过逻辑由 browser_client._apply_filters 单元逻辑保证（filters 原样到达）
        assert got["filters"] == {
            "sort": "综合", "note_type": "全部", "time_range": "",
            "scope": "不限", "distance": "",
        }


    def test_collect_progress_missing(self, client):
        r = client.get("/api/collect_progress", params={"task_id": "nope"})
        assert r.json()["ok"] is False

    def test_collect_stop_running(self, client):
        server._COLLECT_TASKS["t1"] = {"status": "running", "keyword": "k", "limit": 5,
                                        "collected": 0, "note": "", "stop_event": False}
        r = client.post("/api/collect_stop", json={"task_id": "t1"})
        assert r.json()["ok"] is True and server._COLLECT_TASKS["t1"]["stop_event"] is True

    def test_collect_stop_idle(self, client):
        r = client.post("/api/collect_stop", json={"task_id": "t2"})
        assert r.json()["ok"] is False


class TestNotes:
    def test_notes_all(self, client, tmp_path):
        _seed_notes(tmp_path)
        r = client.get("/api/notes")
        assert r.json()["total"] == 2

    def test_notes_keyword(self, client, tmp_path):
        _seed_notes(tmp_path)
        r = client.get("/api/notes", params={"keyword": "翡翠"})
        assert r.json()["total"] == 1

    def test_notes_tag(self, client, tmp_path):
        _seed_notes(tmp_path)
        r = client.get("/api/notes", params={"tag": "潜力"})
        assert r.status_code == 200 and r.json()["total"] >= 0

    def test_notes_order_fetched(self, client, tmp_path):
        _seed_notes(tmp_path)
        r = client.get("/api/notes", params={"order": "fetched_at"})
        assert r.status_code == 200

    def test_notes_local_cover(self, client, tmp_path):
        _seed_notes(tmp_path)
        (tmp_path / "images" / "a1_cover.jpg").write_bytes(b"img")
        r = client.get("/api/notes")
        note = next(x for x in r.json()["notes"] if x["feed_id"] == "a1")
        assert note["local_cover"] == "/app/images/a1_cover.jpg"


class TestDownloadImages:
    def test_download_empty(self, client):
        r = client.post("/api/download_images", json={"feed_ids": []})
        assert r.status_code == 400

    def test_download_mixed(self, client, tmp_path):
        (tmp_path / "images" / "zz_cover.jpg").write_bytes(b"img")
        r = client.post("/api/download_images", json={"feed_ids": ["zz", "yy"]})
        assert "yy" in json.loads(r.headers["X-Missing"])

    def test_download_zip(self, client, tmp_path):
        (tmp_path / "images" / "zz_cover.jpg").write_bytes(b"img")
        r = client.post("/api/download_images", json={"feed_ids": ["zz"]})
        assert r.status_code == 200 and r.content.startswith(b"PK")


class TestNoteDetail:
    def test_detail_404(self, client):
        assert client.get("/api/note/nope").status_code == 404

    def test_detail_enriched(self, client, monkeypatch, tmp_path):
        _seed_notes(tmp_path)

        async def fake_detail(n):
            return {**n, "detail_imgs": ["http://img/1"], "desc": "新描述", "title": "新标题"}

        monkeypatch.setattr(server, "get_feed_detail", fake_detail)
        r = client.get("/api/note/a1")
        j = r.json()
        assert j["images"] == ["http://img/1"] and j["desc"] == "新描述" and j["title"] == "新标题"

    def test_detail_fallback(self, client, monkeypatch, tmp_path):
        _seed_notes(tmp_path)

        async def boom(n):
            raise RuntimeError("detail 失败")

        monkeypatch.setattr(server, "get_feed_detail", boom)
        r = client.get("/api/note/a1")
        assert r.json()["feed_id"] == "a1" and "images" not in r.json()


class TestClone:
    def test_clone_404(self, client):
        r = client.post("/api/clone", json={"feed_id": "nope"})
        assert r.status_code == 404

    def test_clone_ok(self, client, monkeypatch, tmp_path):
        _seed_notes(tmp_path)

        async def fake_collect(n, fid):
            return ["http://img/1", "http://img/2"]

        monkeypatch.setattr(server, "_collect_images", fake_collect)
        monkeypatch.setattr(server, "_download_images", lambda fid, imgs: ["/app/images/a1_p0.jpg"])
        r = client.post("/api/clone", json={"feed_id": "a1"})
        j = r.json()
        assert j["detail_ok"] is True and j["images_local"] == ["/app/images/a1_p0.jpg"]

    def test_clone_fallback_cover(self, client, monkeypatch, tmp_path):
        _seed_notes(tmp_path)

        async def boom(n):
            raise RuntimeError("detail 失败")

        monkeypatch.setattr(server, "get_feed_detail", boom)
        monkeypatch.setattr(server, "_download_images", lambda fid, imgs: [])
        r = client.post("/api/clone", json={"feed_id": "a1"})
        j = r.json()
        assert j["detail_ok"] is False  # 无详情图片，detail_ok=False

    def test_clone_video_downloads(self, client, monkeypatch, tmp_path):
        """视频笔记克隆：下载原视频到本地（读操作），返回 video_file；上传通道 P 级待办。"""
        con = sqlite3.connect(tmp_path / "test.db")
        server._init_tables(con)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        _rank_and_insert(con, {"feed_id": "v1", "title": "视频笔记", "liked_count": "100",
                               "fans_count": "1000", "type": "video",
                               "video_url": "https://sns-video-hw.xhscdn.com/v1.mp4"}, "翡翠", now)
        con.commit()
        con.close()
        async def fake_collect(n, fid):
            return []

        monkeypatch.setattr(server, "_collect_images", fake_collect)
        monkeypatch.setattr(server, "_download_images", lambda fid, imgs: [])
        monkeypatch.setattr(server, "_download_video", lambda fid, url: "/videos/v1.mp4")
        r = client.post("/api/clone", json={"feed_id": "v1"})
        assert r.status_code == 200
        j = r.json()
        assert j["video_file"] == "/videos/v1.mp4"
        assert j["note_type"] == "video"

    def test_clone_video_without_url_fetch_detail(self, client, monkeypatch, tmp_path):
        """视频笔记缺 video_url（老库）→ 详情页回填后下载。"""
        con = sqlite3.connect(tmp_path / "test.db")
        server._init_tables(con)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        _rank_and_insert(con, {"feed_id": "v1", "title": "视频笔记", "liked_count": "100",
                               "fans_count": "1000", "type": "video"}, "翡翠", now)
        con.commit()
        con.close()
        async def fake_collect(n, fid):
            return []

        monkeypatch.setattr(server, "_collect_images", fake_collect)
        monkeypatch.setattr(server, "_download_images", lambda fid, imgs: [])

        async def fake_detail(n):
            n["video_url"] = "https://sns-video-hw.xhscdn.com/v2.mp4"
            return n

        monkeypatch.setattr(server, "get_feed_detail", fake_detail)
        monkeypatch.setattr(server, "_download_video", lambda fid, url: "/videos/v1.mp4")
        r = client.post("/api/clone", json={"feed_id": "v1"})
        assert r.status_code == 200
        assert r.json()["video_file"] == "/videos/v1.mp4"

    def test_download_video_ok(self, client, monkeypatch, tmp_path):
        """_download_video：真实下载字节 → 返回 /videos/ 代理路径。"""
        import http.server
        import threading

        video_bytes = b"\x00\x00\x00\x18ftypmp42fakevideodata" * 200  # >1KB
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(video_bytes)))
                self.end_headers()
                self.wfile.write(video_bytes)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setattr(server, "VIDEOS_DIR", tmp_path / "videos")
        (tmp_path / "videos").mkdir(exist_ok=True)
        out = server._download_video("v1", f"http://127.0.0.1:{port}/v.mp4")
        assert out == "/videos/v1.mp4"
        assert (tmp_path / "videos" / "v1.mp4").stat().st_size == len(video_bytes)
        srv.shutdown()

    def test_download_video_empty_url(self, client, monkeypatch, tmp_path):
        """_download_video：空地址/失败 → 空串（不炸）。"""
        monkeypatch.setattr(server, "VIDEOS_DIR", tmp_path / "videos")
        assert server._download_video("v1", "") == ""


class TestPublishStats:
    """P1 数据回流：发布记录回写互动数据（浏览/赞/藏/评/转）。"""

    @staticmethod
    def _seed_log(tmp_path, feed_id="a1"):
        _seed_notes(tmp_path)
        con = sqlite3.connect(tmp_path / "test.db")
        con.execute(
            "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (feed_id, "标题", "正文", "", "公开可见", "success", "2026-10-28T12:00:00"),
        )
        con.commit()
        con.close()

    def test_stats_by_source_feed(self, client, monkeypatch, tmp_path):
        self._seed_log(tmp_path, "a1")

        async def fake_detail(note):
            note.update({"views": "1234", "liked_count": "100", "collected_count": "20",
                         "comment_count": "5", "shared_count": "2"})
            return note

        monkeypatch.setattr(server, "get_feed_detail", fake_detail)
        r = client.post("/api/publish_stats", json={"log_id": 1})
        j = r.json()
        assert r.status_code == 200 and j["ok"] is True and j["stats"]["views"] == "1234"
        con = sqlite3.connect(tmp_path / "test.db")
        row = con.execute("SELECT stats_json FROM publish_logs WHERE id=1").fetchone()
        con.close()
        assert json.loads(row[0])["liked_count"] == "100"

    def test_stats_by_url(self, client, monkeypatch, tmp_path):
        self._seed_log(tmp_path, "")

        async def fake_url(url):
            return {"views": "999", "liked_count": "50", "collected_count": "",
                    "comment_count": "", "shared_count": ""}

        monkeypatch.setattr(server, "get_note_detail_by_url", fake_url)
        r = client.post("/api/publish_stats", json={"log_id": 1, "note_url": "https://www.xiaohongshu.com/explore/abc"})
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["stats"]["views"] == "999"

    def test_stats_404(self, client):
        r = client.post("/api/publish_stats", json={"log_id": 999})
        assert r.status_code == 404


class TestCollectClients:
    def test_clients_ok(self, client, monkeypatch, tmp_path):
        _seed_notes(tmp_path)
        from src import browser_client

        async def fake_clients(feed_id, token):
            return [{"uid": "u1", "nickname": "n1", "comment": "c1", "profile": "p1"}]

        monkeypatch.setattr(browser_client, "clients", fake_clients)
        r = client.post("/api/clients", json={"feed_id": "a1"})
        j = r.json()
        assert j["total"] == 1 and j["new"] == 1
        con = sqlite3.connect(tmp_path / "test.db")
        cnt = con.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        con.close()
        assert cnt == 1

    def test_clients_fail(self, client, monkeypatch):
        from src import browser_client

        async def boom(feed_id, token):
            raise RuntimeError("浏览器失败")

        monkeypatch.setattr(browser_client, "clients", boom)
        r = client.post("/api/clients", json={"feed_id": "x"})
        assert r.status_code == 502


class TestCookieInfo:
    def test_cookie_info_with_data(self, client, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "cookies.json").write_text(
            json.dumps({"cookies": [{"name": "web_session", "value": "abcdef012345"}]}))
        r = client.get("/api/cookie_info")
        j = r.json()
        assert j["ok"] is True and j["count"] == 1 and j["account"].startswith("abcdef01")

    def test_cookie_info_missing_file(self, client):
        r = client.get("/api/cookie_info")
        assert r.json()["ok"] is True and r.json()["count"] == 0

    def test_cookie_info_bad_json(self, client, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "cookies.json").write_text("{broken")
        r = client.get("/api/cookie_info")
        j = r.json()
        assert j["ok"] is False and "失败" in j["error"]


class TestImportCookie:
    def test_import_empty(self, client):
        r = client.post("/api/import_cookie", json={"cookie_text": "  "})
        assert r.status_code == 400

    def test_import_unparseable(self, client):
        r = client.post("/api/import_cookie", json={"cookie_text": "无等号"})
        assert r.status_code == 400

    def test_import_invalid(self, client, monkeypatch):
        async def fake_verify(cookies):
            return {"logged_in": False, "url": "u", "hasLoginUi": True}

        monkeypatch.setattr(server, "_verify_cookies_or_raise", fake_verify)
        r = client.post("/api/import_cookie", json={"cookie_text": "web_session=abc; a1=def"})
        j = r.json()
        assert j["ok"] is False and j["valid"] is False

    def test_import_ok(self, client, monkeypatch, tmp_path):
        async def fake_verify(cookies):
            return {"logged_in": True}

        monkeypatch.setattr(server, "_verify_cookies_or_raise", fake_verify)
        r = client.post("/api/import_cookie", json={"cookie_text": "web_session=abc; a1=def"})
        j = r.json()
        assert j["ok"] is True and j["valid"] is True and j["count"] == 2
        assert (tmp_path / "data" / "cookies.json").exists()


class TestRefreshCookie:
    def test_refresh_need_auth(self, client, monkeypatch):
        monkeypatch.setattr(server, "_read_chrome_cookies",
                            lambda: (None, {"ok": False, "need_auth": True, "error": "需要授权"}))
        r = client.post("/api/refresh_cookie_from_chrome")
        assert r.json()["need_auth"] is True

    def test_refresh_no_cookies(self, client, monkeypatch):
        monkeypatch.setattr(server, "_read_chrome_cookies", lambda: ([], None))
        r = client.post("/api/refresh_cookie_from_chrome")
        assert r.json()["ok"] is False and "未找到" in r.json()["error"]

    def test_refresh_invalid(self, client, monkeypatch):
        monkeypatch.setattr(server, "_read_chrome_cookies",
                            lambda: ([{"name": "a1", "value": "x"}], None))

        async def fake_verify(cookies):
            return {"logged_in": False, "url": "u", "hasLoginUi": True}

        monkeypatch.setattr(server, "_verify_cookies_or_raise", fake_verify)
        r = client.post("/api/refresh_cookie_from_chrome")
        assert r.json()["valid"] is False

    def test_refresh_ok(self, client, monkeypatch, tmp_path):
        from src import cookie_reader

        monkeypatch.setattr(server, "_read_chrome_cookies",
                            lambda: ([{"name": "web_session", "value": "abc"}], None))
        monkeypatch.setattr(cookie_reader, "cookies_to_text", lambda c: "web_session=abc")

        async def fake_verify(cookies):
            return {"logged_in": True}

        monkeypatch.setattr(server, "_verify_cookies_or_raise", fake_verify)
        r = client.post("/api/refresh_cookie_from_chrome")
        j = r.json()
        assert j["ok"] is True and j["source"] == "chrome" and j["account"].startswith("abc")


class TestLoginStatus:
    """R 批 v1.5 后 /api/login_status 走引擎 CDP 判定（见 _engine_login_state）；
    文件链路 _read_cookie_status 已停用留档，此处直测其本体（旧链路 expires 语义）。"""

    def test_ws_without_expires(self, tmp_path):
        # web_session 无 expires 字段（exps 空）→ exp_ok=True 视为已登录（or 分支应被杀）
        d = tmp_path / "data"
        d.mkdir()
        (d / "cookies.json").write_text(
            json.dumps({"cookies": [{"name": "web_session", "value": "abcdef012345"}]}))
        logged_in, _, exp_ok = server._read_cookie_status(d / "cookies.json")
        assert logged_in is True

    def test_ws_expires_future(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "cookies.json").write_text(
            json.dumps({"cookies": [{"name": "web_session", "value": "x",
                                     "expires": int(time.time()) + 3600}]}))
        logged_in, _, exp_ok = server._read_cookie_status(d / "cookies.json")
        assert logged_in is True

    def test_ws_expires_past(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "cookies.json").write_text(
            json.dumps({"cookies": [{"name": "web_session", "value": "x",
                                     "expires": int(time.time()) - 60}]}))
        logged_in, _, exp_ok = server._read_cookie_status(d / "cookies.json")
        assert logged_in is False


class TestReadChromeCookies:
    def test_pending_auth(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XHS_PROFILE_DIR", str(tmp_path / "p"))
        from src import cookie_reader

        def boom(*a, **k):
            raise cookie_reader.ChromeCookieReadError("未找到库")

        monkeypatch.setattr(cookie_reader, "read_chrome_cookies", boom)
        monkeypatch.setattr(cookie_reader, "keychain_status", lambda: "pending")
        cookies, err = server._read_chrome_cookies()
        assert cookies is None and err["need_auth"] is True and "始终允许" in err["error"]

    def test_other_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XHS_PROFILE_DIR", str(tmp_path / "p"))
        from src import cookie_reader

        def boom(*a, **k):
            raise cookie_reader.ChromeCookieReadError("解密失败")

        monkeypatch.setattr(cookie_reader, "read_chrome_cookies", boom)
        monkeypatch.setattr(cookie_reader, "keychain_status", lambda: "denied")
        cookies, err = server._read_chrome_cookies()
        assert cookies is None and err["need_auth"] is False and "读取 Chrome 登录态失败" in err["error"]


class TestSaveDraft:
    def test_save_draft(self, client):
        r = client.post("/api/save_draft", json={"title": "标题", "content": "内容", "tags": ["a"], "visibility": "仅自己可见"})
        assert r.json()["ok"] is True
        con = sqlite3.connect(str(server.DB_PATH))
        row = con.execute("SELECT title FROM drafts").fetchone()
        con.close()
        assert row[0] == "标题"


class TestPublish:
    _NOON = datetime.datetime(2026, 1, 1, 12, 0)

    def test_publish_risk_blocked(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "human_delay", lambda lo=1.0, hi=3.0: None)
        monkeypatch.setattr(server, "_now", lambda: self._NOON)
        con = sqlite3.connect(tmp_path / "test.db")
        con.row_factory = sqlite3.Row
        server._init_tables(con)
        _seed_publish_log(con, "failed", raw="发布失败，出现验证码拦截")
        con.close()
        r = client.post("/api/publish", json={"title": "t", "content": "c"})
        j = r.json()
        assert j["ok"] is False and "风控熔断" in j["raw"]

    def test_publish_daily_quota(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "human_delay", lambda lo=1.0, hi=3.0: None)
        monkeypatch.setattr(server, "_now", lambda: self._NOON)
        # 上次发布 6 小时前（同一天）→ 冷却通过，命中今日配额上限
        past = (self._NOON - datetime.timedelta(hours=6)).isoformat(timespec="seconds")
        con = sqlite3.connect(tmp_path / "test.db")
        con.row_factory = sqlite3.Row
        server._init_tables(con)
        for _ in range(2):
            _seed_publish_log(con, "success", created_at=past)
        con.close()
        r = client.post("/api/publish", json={"title": "t", "content": "c"})
        assert "已达上限" in r.json()["raw"]

    def test_publish_pass_guard(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "human_delay", lambda lo=1.0, hi=3.0: None)
        monkeypatch.setattr(server, "_now", lambda: self._NOON)
        monkeypatch.setattr(server.random, "uniform", lambda lo, hi: 0.0)
        r = client.post("/api/publish", json={"title": "标题", "content": "正文"})
        j = r.json()
        # 发布已迁移手动：ok 恒 False，raw 提示去小红书发布页
        assert j["ok"] is False and "发布页" in j["raw"]
        con = sqlite3.connect(tmp_path / "test.db")
        cnt = con.execute("SELECT COUNT(*) FROM publish_logs").fetchone()[0]
        con.close()
        assert cnt == 1


class TestLogs:
    def test_logs_empty(self, client):
        assert client.get("/api/logs").json()["logs"] == []

    def test_logs_with_data(self, client, tmp_path):
        con = sqlite3.connect(tmp_path / "test.db")
        con.row_factory = sqlite3.Row
        server._init_tables(con)
        _seed_publish_log(con, "success")
        con.close()
        r = client.get("/api/logs")
        assert len(r.json()["logs"]) == 1 and r.json()["logs"][0]["status"] == "success"


class TestImgProxy:
    def test_bad_path(self, client):
        assert client.get("/api/img", params={"path": "/etc/passwd"}).status_code == 400
        assert client.get("/api/img", params={"path": ""}).status_code == 400

    def test_missing(self, client):
        r = client.get("/api/img", params={"path": "/app/images/nope.jpg"})
        assert r.status_code == 404

    def test_ok(self, client, tmp_path):
        (tmp_path / "images" / "a.jpg").write_bytes(b"jpeg")
        r = client.get("/api/img", params={"path": "/app/images/a.jpg"})
        assert r.status_code == 200 and r.content == b"jpeg"


class TestRankInsertBranches:
    def test_rank_fail_fallback(self, monkeypatch, tmp_path):
        con = sqlite3.connect(tmp_path / "t.db")
        con.row_factory = sqlite3.Row
        server._init_tables(con)

        def boom(notes):
            raise ValueError("评分失败")

        monkeypatch.setattr(server, "rank_notes", boom)
        assert server._rank_and_insert(con, {"feed_id": "z", "title": "t"}, "k", "2026-01-01") is True
        row = con.execute("SELECT tag FROM notes WHERE feed_id='z'").fetchone()
        assert row[0] == "评分失败"


class TestCleanupBranches:
    def _setup(self, monkeypatch, tmp_path, images_dir=None):
        monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
        d = images_dir or tmp_path / "images"
        monkeypatch.setattr(server, "IMAGES_DIR", d)
        d.mkdir(exist_ok=True)
        return d

    def test_cleanup_no_images_dir(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(server, "IMAGES_DIR", tmp_path / "no_exist")
        assert server._cleanup_orphan_images() == 0

    def test_cleanup_skips_dirs(self, monkeypatch, tmp_path):
        d = self._setup(monkeypatch, tmp_path, images_dir=tmp_path / "no_exist")
        (d / "sub").mkdir()
        assert server._cleanup_orphan_images() == 0

    def test_cleanup_removes_orphan(self, monkeypatch, tmp_path):
        d = self._setup(monkeypatch, tmp_path, images_dir=tmp_path / "no_exist")
        (d / "orphan_cover.jpg").write_bytes(b"x")
        assert server._cleanup_orphan_images() == 1

    def test_cleanup_keeps_referenced(self, monkeypatch, tmp_path):
        d = self._setup(monkeypatch, tmp_path, images_dir=tmp_path / "no_exist")
        _seed_notes(tmp_path)
        (d / "a1_cover.jpg").write_bytes(b"x")
        (d / "b2_cover.jpg").write_bytes(b"x")
        (d / "orphan_cover.jpg").write_bytes(b"x")
        assert server._cleanup_orphan_images() == 1
