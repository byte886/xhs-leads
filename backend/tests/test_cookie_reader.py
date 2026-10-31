"""Chrome Cookie 读取链路单测：平台路径、密钥状态、解密分支、库选择、文本转换。"""
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from src import cookie_reader


class TestUserDataDir:
    def test_override(self, monkeypatch):
        monkeypatch.setenv("XHS_CHROME_USER_DATA_DIR", "~/fake")
        assert cookie_reader._chrome_user_data_dir() == str(Path("~/fake").expanduser())

    def test_macos(self, monkeypatch):
        monkeypatch.delenv("XHS_CHROME_USER_DATA_DIR", raising=False)
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Darwin")
        assert cookie_reader._chrome_user_data_dir() == os.path.expanduser(
            "~/Library/Application Support/Google/Chrome")

    def test_windows(self, monkeypatch):
        monkeypatch.delenv("XHS_CHROME_USER_DATA_DIR", raising=False)
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Windows")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\t\\AppData\\Local")
        assert cookie_reader._chrome_user_data_dir() == os.path.join(
            "C:\\Users\\t\\AppData\\Local", "Google", "Chrome", "User Data")

    def test_unsupported(self, monkeypatch):
        monkeypatch.delenv("XHS_CHROME_USER_DATA_DIR", raising=False)
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Linux")
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._chrome_user_data_dir()


class TestCookiesToText:
    def test_joins_with_semicolons(self):
        c = [{"name": "web_session", "value": "abc"}, {"name": "a1", "value": ""},
             {"name": "xhs", "value": "def"}]
        assert cookie_reader.cookies_to_text(c) == "web_session=abc; xhs=def"


class TestDecryptBlob:
    def test_v10_uses_decrypt(self, monkeypatch):
        monkeypatch.setattr(cookie_reader, "_decrypt_v10", lambda k, b, s: b"secret")
        assert cookie_reader._decrypt_cookie_blob(b"k", b"v10xyz", None, True) == "secret"

    def test_plain_value(self):
        assert cookie_reader._decrypt_cookie_blob(b"k", b"", "plain", False) == "plain"

    def test_decode_error_returns_empty(self, monkeypatch):
        def bad(key, blob, strip):
            raise UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")
        monkeypatch.setattr(cookie_reader, "_decrypt_v10", bad)
        assert cookie_reader._decrypt_cookie_blob(b"k", b"v10x", None, True) == ""


class TestKeychainStatus:
    def test_granted(self, monkeypatch):
        r = subprocess.CompletedProcess(["security"], 0, stdout="key\n", stderr="")
        monkeypatch.setattr(cookie_reader.subprocess, "run", lambda *a, **k: r)
        assert cookie_reader.keychain_status() == "granted"

    def test_unavailable(self, monkeypatch):
        r = subprocess.CompletedProcess(["security"], 1, stdout="", stderr="err")
        monkeypatch.setattr(cookie_reader.subprocess, "run", lambda *a, **k: r)
        assert cookie_reader.keychain_status() == "unavailable"

    def test_pending_timeout(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(["security"], 5)
        monkeypatch.setattr(cookie_reader.subprocess, "run", raise_timeout)
        assert cookie_reader.keychain_status() == "pending"


@pytest.fixture()
def fake_profiles(tmp_path, monkeypatch):
    """临时 Chrome 用户目录：Default 旧 web_session + Profile 1 新 web_session。"""
    def _make(profile, creation):
        d = tmp_path / profile
        d.mkdir(parents=True)
        con = sqlite3.connect(d / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, creation_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?)",
                    (".xiaohongshu.com", "web_session", creation))
        con.commit()
        con.close()
    _make("Default", 100)
    _make("Profile 1", 900)
    monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
    return tmp_path


class TestPickBestProfile:
    def test_picks_newest_session(self, fake_profiles):
        assert cookie_reader._pick_best_profile_db().endswith("Profile 1/Cookies")

    def test_equal_creation_keeps_first(self, tmp_path, monkeypatch):
        # 两 profile 相同 creation 时间：保留先遍历者（Default）（>->= 应被杀）
        for prof in ("Default", "Profile 1"):
            d = tmp_path / prof
            d.mkdir(parents=True)
            con = sqlite3.connect(d / "Cookies")
            con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, creation_utc INTEGER)")
            con.execute("INSERT INTO cookies VALUES (?,?,?)",
                        (".xiaohongshu.com", "web_session", 500))
            con.commit()
            con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        assert cookie_reader._pick_best_profile_db().endswith("Default/Cookies")

    def test_no_db_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._pick_best_profile_db()


class TestReadChromeCookies:
    def test_plain_and_v10(self, tmp_path, monkeypatch):
        db = tmp_path / "Cookies"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        con.execute("INSERT INTO meta VALUES ('version','25')")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                    "encrypted_value BLOB, path TEXT, expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                    (".xiaohongshu.com", "plain_cookie", "pv", b"", "/", 0))
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                    (".xiaohongshu.com", "enc_cookie", "", b"v10abc", "/", 0))
        con.commit()
        con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_cookie_db_path", lambda: str(db))
        monkeypatch.setattr(cookie_reader, "_get_decryption_key", lambda: b"k" * 16)
        monkeypatch.setattr(cookie_reader, "_decrypt_v10", lambda k, b, s: b"decoded")
        out = cookie_reader.read_chrome_cookies()
        assert {c["name"]: c["value"] for c in out} == {"plain_cookie": "pv", "enc_cookie": "decoded"}
