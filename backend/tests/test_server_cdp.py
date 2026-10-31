"""CDP 自动化配置层纯逻辑测试（无真实浏览器/网络：全部 monkeypatch）。

目的：把 server.py 覆盖率从 72% 拉到 ≥80% 门。覆盖 _probe_cdp / DevToolsActivePort
读取 / 平台脚本定位 / pick/status/setup/open_inspect 各分支（对应质检报告遗留项）。
"""
import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from src import server


@pytest.fixture()
def client(monkeypatch):
    """TestClient：真实浏览器已由 conftest 全局禁用（cleanup_tabs noop）。"""
    from src.server import app
    with TestClient(app) as c:
        yield c


# ---------- _probe_cdp ----------
class TestProbeCdp:
    def test_success(self):
        # urlopen() 返回 fake；with 块绑定 __enter__() 结果 → __enter__ 必须返回自身
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__ = mock.MagicMock(return_value=resp)

        with mock.patch.object(server.urllib.request, "urlopen", return_value=resp):
            assert asyncio.run(server._probe_cdp(9223)) is True

    def test_conn_error(self):
        with mock.patch.object(
            server.urllib.request, "urlopen", side_effect=ConnectionRefusedError
        ):
            assert asyncio.run(server._probe_cdp(1)) is False

    def test_timeout(self):
        with mock.patch.object(
            server.urllib.request, "urlopen", side_effect=TimeoutError
        ):
            assert asyncio.run(server._probe_cdp(1)) is False


# ---------- _read_devtools_active_port ----------
class TestDevtoolsActivePort:
    def _no_user_chrome(self, monkeypatch):
        # 防止兜底追加真实用户 Chrome 目录（本机 9222 在跑 → 会读到它）
        monkeypatch.setattr(server.sys, "platform", "linux")

    def test_reads_port(self, tmp_path, monkeypatch):
        self._no_user_chrome(monkeypatch)
        (tmp_path / "DevToolsActivePort").write_text("9223\n/devtools/browser/uuid\n", encoding="utf8")
        assert server._read_devtools_active_port(str(tmp_path)) == 9223

    def test_empty_or_bad_content(self, tmp_path, monkeypatch):
        self._no_user_chrome(monkeypatch)
        (tmp_path / "DevToolsActivePort").write_text("not-a-number\n", encoding="utf8")
        assert server._read_devtools_active_port(str(tmp_path)) is None

    def test_missing_file(self, tmp_path, monkeypatch):
        self._no_user_chrome(monkeypatch)
        assert server._read_devtools_active_port(str(tmp_path / "nope")) is None

    def test_corrupt_file_skips(self, tmp_path, monkeypatch):
        # 读取抛异常 → 跳过该候选，返回 None
        self._no_user_chrome(monkeypatch)
        monkeypatch.setattr(server.Path, "exists", lambda self: True)
        p = tmp_path / "DevToolsActivePort"
        p.write_text("123\n", encoding="utf8")

        def _bad_read(encoding=None):
            raise OSError("boom")

        monkeypatch.setattr(server.Path, "read_text", _bad_read)
        assert server._read_devtools_active_port(str(tmp_path)) is None


# ---------- 平台脚本定位 ----------
class TestPlatformScripts:
    def test_setup_script_exists(self):
        p = server._platform_setup_script()
        assert p is not None and p.name == "setup_chrome.py"

    def test_pick_script_exists(self):
        p = server._pick_chrome_script()
        assert p is not None and p.name == "pick_chrome.py"


# ---------- _pick_chrome_file ----------
# ---------- cdp_status 分支 ----------
class TestCdpStatus:
    def test_ready(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = True
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=True)):
                r = client.get("/api/cdp_status")
        assert r.json()["state"] == "ready" and r.json()["port_ready"] is True

    def test_need_chrome(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = None
        bc.chrome_running.return_value = False
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                r = client.get("/api/cdp_status")
        assert r.json()["state"] == "need_chrome"

    def test_not_running(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = False
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    r = client.get("/api/cdp_status")
        assert r.json()["state"] == "not_running"

    def test_need_auth(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = True
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    r = client.get("/api/cdp_status")
        assert r.json()["state"] == "need_auth"


# ---------- cdp_pick_chrome 路由 ----------
class TestCdpPickChromeRoute:
    def test_pick_cancel(self, client, monkeypatch):
        monkeypatch.setattr(server, "_pick_chrome_file", lambda: None)
        r = client.post("/api/cdp_pick_chrome")
        assert r.json()["ok"] is False and "已取消" in r.json()["note"]

    def test_pick_saved(self, client, monkeypatch):
        bc = mock.MagicMock()
        monkeypatch.setattr(server, "_pick_chrome_file", lambda: "/Applications/Google Chrome.app")
        with mock.patch("src.browser_client", bc):
            r = client.post("/api/cdp_pick_chrome")
        assert r.json()["ok"] is True and r.json()["chrome_path"] == "/Applications/Google Chrome.app"
        bc.save_cdp_config.assert_called_once_with(chrome_path="/Applications/Google Chrome.app")


# ---------- cdp_open_inspect ----------
class TestCdpOpenInspect:
    def test_no_chrome(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc.chrome_executable.return_value = None
        with mock.patch("src.browser_client", bc):
            r = client.post("/api/cdp_open_inspect")
        assert r.json()["ok"] is False and "未找到 Chrome" in r.json()["note"]

    def test_open(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.open_inspect_remote_debugging = mock.AsyncMock(return_value=True)
        with mock.patch("src.browser_client", bc):
            r = client.post("/api/cdp_open_inspect")
        assert r.json()["ok"] is True
        bc.open_inspect_remote_debugging.assert_called_once()


# ---------- cdp_setup 分支 ----------
class TestCdpSetup:
    def test_ready(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=True)):
                r = client.post("/api/cdp_setup", json={"restart": False})
        assert r.json()["status"] == "ready"
        bc.save_cdp_config.assert_not_called()

    def test_saves_custom_paths(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=True)):
                r = client.post("/api/cdp_setup", json={"restart": False, "chrome_path": "/x", "profile_dir": "/y"})
        assert r.json()["status"] == "ready"
        bc.save_cdp_config.assert_called_once_with(chrome_path="/x", profile_dir="/y")

    def test_chrome_not_found(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = None
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    r = client.post("/api/cdp_setup", json={"restart": False})
        assert r.json()["ok"] is False and "未找到 Chrome" in r.json()["note"]

    def test_need_restart(self, client, monkeypatch):
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = True
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    r = client.post("/api/cdp_setup", json={"restart": False})
        assert r.json()["need_restart"] is True

    def test_restart_then_launch_ok(self, client, monkeypatch):
        """restart=true：退出 Chrome → 平台脚本启动 → 轮询探测成功。"""
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = True
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(side_effect=[False, True])):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    with mock.patch.object(
                        server.subprocess, "run",
                        return_value=mock.MagicMock(stdout="LAUNCHED\n"),
                    ):
                        with mock.patch.object(server.asyncio, "sleep", mock.AsyncMock()):
                            r = client.post("/api/cdp_setup", json={"restart": True})
        assert r.json()["status"] == "ready"
        bc.quit_chrome.assert_called_once()

    def test_script_not_found(self, client, monkeypatch):
        """脚本 CHROME_NOT_FOUND → 明确报错。"""
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = False
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    with mock.patch.object(
                        server.subprocess, "run",
                        return_value=mock.MagicMock(stdout="CHROME_NOT_FOUND\n"),
                    ):
                        r = client.post("/api/cdp_setup", json={"restart": False})
        assert r.json()["ok"] is False and "未找到 Chrome" in r.json()["note"]

    def test_launch_timeout(self, client, monkeypatch):
        """启动后轮询 30s 仍未探测到 → 超时提示。"""
        bc = mock.MagicMock()
        bc._profile_settings.return_value = (9223, "/tmp/p")
        bc.chrome_executable.return_value = "/Applications/Google Chrome.app"
        bc.chrome_running.return_value = False
        with mock.patch("src.browser_client", bc):
            with mock.patch.object(server, "_probe_cdp", mock.AsyncMock(return_value=False)):
                with mock.patch.object(server, "_read_devtools_active_port", return_value=None):
                    with mock.patch.object(
                        server.subprocess, "run",
                        return_value=mock.MagicMock(stdout="LAUNCHED\n"),
                    ):
                        with mock.patch.object(server.asyncio, "sleep", mock.AsyncMock()):
                            r = client.post("/api/cdp_setup", json={"restart": False})
        assert r.json()["ok"] is False and "未检测到远程调试开启" in r.json()["note"]


# ---------- login_status 异常分支 ----------
class TestLoginStatusError:
    def test_cookie_read_error(self, client, monkeypatch):
        monkeypatch.setattr(server, "_LOGIN_CACHE", {"data": None, "t": 0.0})

        def boom(p):
            raise ValueError("bad json")

        monkeypatch.setattr(server, "_read_cookie_status", boom)
        r = client.get("/api/login_status")
        assert r.json()["status"] == "unknown" and "失败" in r.json()["raw"]


# ---------- 真实 cookies.json 解析（补 _read_cookie_status 分支） ----------
class TestCookieStatusReal:
    def test_missing_file(self, tmp_path):
        assert server._read_cookie_status(tmp_path / "cookies.json") == (False, "", True)

    def test_valid_session(self, tmp_path):
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "abc123"}]}), encoding="utf8")
        assert server._read_cookie_status(p) == (True, "abc123", True)

    def test_expired_session(self, tmp_path):
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "abc", "expires": 1}]}), encoding="utf8")
        assert server._read_cookie_status(p) == (False, "abc", False)

    def test_no_web_session(self, tmp_path):
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps({"cookies": [{"name": "other", "value": "x"}]}), encoding="utf8")
        assert server._read_cookie_status(p) == (False, "", True)
