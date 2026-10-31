"""cookie_reader 补充测试：v10 解密往返、平台路由、钥匙串/DPAPI 分支、read 边界。"""
import base64
import json
import sqlite3
import subprocess

import pytest

from src import cookie_reader


def _encrypt_v10(key: bytes, plain: bytes, strip_integrity: bool) -> bytes:
    """与 _decrypt_v10 对称的加密助手：构造 v10 blob 用于往返测试。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    payload = plain
    if strip_integrity:
        payload = b"\x00" * 32 + payload
    pad = 16 - len(payload) % 16
    payload += bytes([pad]) * pad
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    dk = kdf.derive(key)
    enc = Cipher(algorithms.AES(dk), modes.CBC(b" " * 16)).encryptor().update(payload)
    return b"v10" + enc


class TestDecryptV10:
    def test_roundtrip_plain(self):
        key = b"k" * 32
        blob = _encrypt_v10(key, b"secret-cookie-value", strip_integrity=False)
        assert cookie_reader._decrypt_v10(key, blob, False) == b"secret-cookie-value"

    def test_roundtrip_strip_integrity(self):
        key = b"k" * 32
        blob = _encrypt_v10(key, b"value-without-sha", strip_integrity=True)
        assert cookie_reader._decrypt_v10(key, blob, True) == b"value-without-sha"

    def test_bad_header(self):
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._decrypt_v10(b"k" * 32, b"v20notreal", False)


class TestDecryptCookieBlob:
    def test_plain_value_fallback(self):
        assert cookie_reader._decrypt_cookie_blob(b"k", b"", "明文", False) == "明文"

    def test_v10_unicode_error_returns_empty(self, monkeypatch):
        def boom(key, blob, strip):
            return b"\xff\xfe-broken-utf8"

        monkeypatch.setattr(cookie_reader, "_decrypt_v10", boom)
        assert cookie_reader._decrypt_cookie_blob(b"k", b"v10xx", None, False) == ""


class TestKeyMacos:
    def test_ok(self, monkeypatch):
        class R:
            returncode = 0
            stdout = "KEY\n"
            stderr = ""

        monkeypatch.setattr(cookie_reader.subprocess, "run", lambda *a, **k: R())
        assert cookie_reader._key_macos() == b"KEY"

    def test_fail(self, monkeypatch):
        class R:
            returncode = 1
            stdout = ""
            stderr = "denied"

        monkeypatch.setattr(cookie_reader.subprocess, "run", lambda *a, **k: R())
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._key_macos()

    def test_empty_secret(self, monkeypatch):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(cookie_reader.subprocess, "run", lambda *a, **k: R())
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._key_macos()


class TestKeyWindows:
    def test_no_local_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._key_windows()

    def test_bad_prefix(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        d = tmp_path / "Google" / "Chrome" / "User Data"
        d.mkdir(parents=True)
        (d / "Local State").write_text(json.dumps(
            {"os_crypt": {"encrypted_key": base64.b64encode(b"NOT-DPAPI-xx").decode()}}))
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._key_windows()

    def test_local_state_path(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", "C:/Users/t/AppData/Local")
        assert cookie_reader._chrome_local_state_path().endswith("Google/Chrome/User Data/Local State")


class TestGetDecryptionKey:
    def test_darwin(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(cookie_reader, "_key_macos", lambda: b"K")
        assert cookie_reader._get_decryption_key() == b"K"

    def test_windows(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Windows")
        monkeypatch.setattr(cookie_reader, "_key_windows", lambda: b"K")
        assert cookie_reader._get_decryption_key() == b"K"

    def test_unsupported(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Linux")
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader._get_decryption_key()


class TestKeychainStatus:
    def test_non_darwin(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Windows")
        assert cookie_reader.keychain_status() == "unavailable"

    def test_timeout_pending(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Darwin")

        def boom(*a, **k):
            raise subprocess.TimeoutExpired("security", 5)

        monkeypatch.setattr(cookie_reader.subprocess, "run", boom)
        assert cookie_reader.keychain_status() == "pending"

    def test_exception_unavailable(self, monkeypatch):
        monkeypatch.setattr(cookie_reader.platform, "system", lambda: "Darwin")

        def boom(*a, **k):
            raise OSError("x")

        monkeypatch.setattr(cookie_reader.subprocess, "run", boom)
        assert cookie_reader.keychain_status() == "unavailable"


class TestPathHelpers:
    def test_cookie_db_path(self, monkeypatch):
        monkeypatch.setattr(cookie_reader, "_pick_best_profile_db", lambda: "X")
        assert cookie_reader._chrome_cookie_db_path() == "X"

    def test_pick_best_falls_back_to_mtime(self, monkeypatch, tmp_path):
        d = tmp_path / "Default"
        d.mkdir(parents=True)
        con = sqlite3.connect(d / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, creation_utc INTEGER)")
        con.commit()
        con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        # 无 web_session 行 → best=None → 兜底 max(paths, getmtime)
        assert cookie_reader._pick_best_profile_db().endswith("Cookies")


class TestReadChromeCookies:
    def test_no_db(self, monkeypatch):
        monkeypatch.setattr(cookie_reader, "_pick_best_profile_db", lambda: "/no/such/Cookies")
        with pytest.raises(cookie_reader.ChromeCookieReadError):
            cookie_reader.read_chrome_cookies()

    def test_meta_missing_uses_no_strip(self, monkeypatch, tmp_path):
        # meta 表缺失 → strip_integrity=False（读明文 cookie 不剥前缀）
        d = tmp_path / "Default"
        d.mkdir(parents=True)
        con = sqlite3.connect(d / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                    "encrypted_value BLOB, path TEXT, expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                    (".xiaohongshu.com", "web_session", "plainvalue", b"", "/", 0))
        con.commit()
        con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(cookie_reader, "_get_decryption_key", lambda: b"k")
        out = cookie_reader.read_chrome_cookies()
        assert out and out[0]["value"] == "plainvalue" and out[0]["domain"] == ".xiaohongshu.com"

    def test_v10_blob_roundtrip(self, monkeypatch, tmp_path):
        key = b"k" * 32
        blob = _encrypt_v10(key, b"decrypted-web-session", strip_integrity=False)
        d = tmp_path / "Default"
        d.mkdir(parents=True)
        con = sqlite3.connect(d / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                    "encrypted_value BLOB, path TEXT, expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                    (".xiaohongshu.com", "web_session", None, blob, "/", 0))
        con.commit()
        con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(cookie_reader, "_get_decryption_key", lambda: key)
        out = cookie_reader.read_chrome_cookies()
        assert out and out[0]["value"] == "decrypted-web-session"

    def test_empty_value_skipped(self, monkeypatch, tmp_path):
        d = tmp_path / "Default"
        d.mkdir(parents=True)
        con = sqlite3.connect(d / "Cookies")
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                    "encrypted_value BLOB, path TEXT, expires_utc INTEGER)")
        con.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?)",
                    (".xiaohongshu.com", "bad_cookie", "", b"", "/", 0))
        con.commit()
        con.close()
        monkeypatch.setattr(cookie_reader, "_chrome_user_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(cookie_reader, "_get_decryption_key", lambda: b"k")
        assert cookie_reader.read_chrome_cookies() == []
