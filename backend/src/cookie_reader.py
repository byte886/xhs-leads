"""Chrome 登录态读取（方案 A 生产核心，无 CDP 依赖）。

直接读用户日常 Chrome 的 Cookie SQLite 库 + 钥匙串「Chrome Safe Storage」密钥解密，
跨平台（macOS / Windows）。这是生产环境（无豆包、无 CDP、无内嵌浏览器）的 Cookie
续期来源：失效时自动读取 → 校验 → 落盘 cookies.json → 恢复服务。

解密要点（2026-10-27 实测确认）：
1. 钥匙串返回的是 base64 字符串（如 eHWdSjK6wnOs1pmEvZCaBw==），PBKDF2 的 password
   直接用该字符串本身，**不要 base64 解码**（Chrome macOS 的实现细节）。
2. 参数：AES-128-CBC、iv=b' '*16、salt=b'saltysalt'、PBKDF2-SHA1 1003 次、dklen=16。
3. Cookie 库 meta version>=24 时，密文 = AES-CBC(域名SHA256完整性前缀32B + 值 + PKCS7)，
   解密后需剥离前 32 字节。
4. Windows 分支：DPAPI 解密 Local State 的 os_crypt.encrypted_key（结构就位，待实测）。
"""
from __future__ import annotations

import base64
import os
import platform
import sqlite3
import subprocess

# Chrome Safe Storage 服务名（macOS 钥匙串）
KEYCHAIN_SERVICE = "Chrome Safe Storage"
KEYCHAIN_ACCOUNT = "Chrome"


class ChromeCookieReadError(Exception):
    """读取/解密 Chrome Cookie 失败（未授权、库缺失、格式异常等）。"""


# ---------------------------------------------------------------- 平台路径

def _chrome_user_data_dir() -> str:
    """Chrome 用户数据目录（所有 profile 的父目录）。

    支持 XHS_CHROME_USER_DATA_DIR 覆盖（统一独立 profile 浏览器场景：登录态来源与
    采集浏览器同为 .xhs-leads-profile，读同一个 Chrome Cookie 库 + 钥匙串解密）。
    """
    override = os.environ.get("XHS_CHROME_USER_DATA_DIR")
    if override:
        return os.path.expanduser(override)
    if platform.system() == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if platform.system() == "Windows":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google", "Chrome", "User Data",
        )
    raise ChromeCookieReadError(f"不支持的平台: {platform.system()}")


def _chrome_profile_cookie_paths() -> list[str]:
    """列出 Chrome 全部 profile 的 Cookies 库路径（Default + Profile N）。"""
    base = _chrome_user_data_dir()
    profiles = ["Default"] + [f"Profile {i}" for i in range(1, 20)]
    out = []
    for p in profiles:
        f = os.path.join(base, p, "Cookies")
        if os.path.exists(f):
            out.append(f)
    return out


def _pick_best_profile_db() -> str:
    """选择「最可能正在使用」的 profile 的 Cookies 库（多 profile 场景正确读取）。

    规则（实测依据：用户日常 Chrome 可能有 Default + Profile N 并存，各 profile 登录态独立）：
    1. 优先含 xiaohongshu web_session 且创建时间最新的 profile（用户最近登录小红的那个）；
    2. 全部 profile 均无 web_session 时，取 Cookies 文件 mtime 最新的 profile（最近活跃者）。
    避免误读 Default 里 10 天前的旧有效 session，导致「登出后仍判定已登录」。
    """
    paths = _chrome_profile_cookie_paths()
    if not paths:
        raise ChromeCookieReadError("未找到 Chrome Cookie 库，请先安装并登录 Chrome")

    best: tuple[int, str] | None = None
    for f in paths:
        try:
            con = sqlite3.connect(f"file:{f}?immutable=1", uri=True)
            try:
                row = con.execute(
                    "SELECT creation_utc FROM cookies "
                    "WHERE host_key LIKE '%xiaohongshu%' AND name='web_session' "
                    "ORDER BY creation_utc DESC LIMIT 1"
                ).fetchone()
            finally:
                con.close()
            if row and (best is None or row[0] > best[0]):
                best = (row[0], f)
        except Exception:  # noqa: BLE001
            continue
    if best:
        return best[1]
    return max(paths, key=os.path.getmtime)


def _chrome_cookie_db_path() -> str:
    return _pick_best_profile_db()


def _chrome_local_state_path() -> str:
    """Windows DPAPI 解密用（Local State 里的 os_crypt.encrypted_key）"""
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Google", "Chrome", "User Data", "Local State",
    )


# ---------------------------------------------------------------- 密钥获取

def keychain_status() -> str:
    """探测钥匙串授权状态（带短超时，不阻塞）。

    - "granted": 已授权，可直接读取密钥（不弹窗）
    - "pending": 弹窗等待授权（首次调用会触发系统授权弹窗）
    - "unavailable": 钥匙串中无该条目或读取环境异常
    """
    if platform.system() != "Darwin":
        return "unavailable"
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-w",
             "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return "granted"
        return "unavailable"
    except subprocess.TimeoutExpired:
        # 弹窗出现、等待用户确认
        return "pending"
    except Exception:  # noqa: BLE001
        return "unavailable"


def _key_macos() -> bytes:
    """macOS：钥匙串读取「Chrome Safe Storage」密钥。PBKDF2 的 password 直接用 base64 字符串本身。"""
    r = subprocess.run(
        ["security", "find-generic-password", "-w",
         "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise ChromeCookieReadError(
            f"钥匙串读取失败 rc={r.returncode}（可能未授权："
            f"请在弹出窗口中点「始终允许」）: {r.stderr.strip()[:120]}"
        )
    secret = r.stdout.strip()
    if not secret:
        raise ChromeCookieReadError("钥匙串返回空密钥")
    return secret.encode("utf-8")


def _key_windows() -> bytes:
    """Windows：DPAPI 解密 Local State 的 encrypted_key（"DPAPI" 前缀 + 密文）。结构就位，待实测。"""
    try:
        import ctypes
    except ImportError:  # pragma: no cover
        raise ChromeCookieReadError("Windows 分支需要 ctypes")  # noqa: B904
    path = _chrome_local_state_path()
    if not os.path.exists(path):
        raise ChromeCookieReadError("Chrome Local State 不存在，请先使用 Chrome 登录")
    import json
    with open(path, encoding="utf-8") as f:
        enc = base64.b64decode(json.load(f)["os_crypt"]["encrypted_key"])
    if not enc.startswith(b"DPAPI"):
        raise ChromeCookieReadError("encrypted_key 缺少 DPAPI 前缀（Chrome 新版本格式未适配）")

    class DATA_BLOB(ctypes.Structure):  # noqa: N801
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]

    blob_in, blob_out = DATA_BLOB(), DATA_BLOB()
    buf = ctypes.create_string_buffer(enc[5:])
    blob_in.cbData, blob_in.pbData = len(enc) - 5, ctypes.addressof(buf)
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise ChromeCookieReadError("DPAPI 解密失败（当前 Windows 用户与 Chrome 用户不一致？）")
    key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return key  # Windows: AES key 为原始 32 字节


def _get_decryption_key() -> bytes:
    """获取解密密钥（macOS 钥匙串 / Windows DPAPI）。按平台路由。"""
    if platform.system() == "Darwin":
        return _key_macos()
    if platform.system() == "Windows":
        return _key_windows()
    raise ChromeCookieReadError(f"不支持的平台: {platform.system()}")


# ---------------------------------------------------------------- 解密

def _decrypt_v10(key: bytes, blob: bytes, strip_integrity: bool) -> bytes:
    """v10 密文解密：PBKDF2(SHA1, 'saltysalt', 1003, 16) + AES-CBC(iv=16空格)。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    if not blob.startswith(b"v10"):
        raise ChromeCookieReadError(f"非 v10 头: {blob[:3]!r}")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16,
                     salt=b"saltysalt", iterations=1003)
    dk = kdf.derive(key)
    dec = Cipher(algorithms.AES(dk), modes.CBC(b" " * 16)).decryptor().update(blob[3:])
    plain = dec[:-dec[-1]]  # 去 PKCS7 padding
    if strip_integrity:
        plain = plain[32:]  # Chrome 库 version>=24：剥离域名 SHA256 完整性前缀
    return plain


# ---------------------------------------------------------------- 对外读取

def _decrypt_cookie_blob(key: bytes, blob: bytes, value: str | None, strip_integrity: bool) -> str:
    """解密单条 cookie 密文：v10 头走 AES 解密，否则用明文 value。"""
    if blob.startswith(b"v10"):
        try:
            return _decrypt_v10(key, blob, strip_integrity).decode("utf-8")
        except UnicodeDecodeError:
            return ""  # 解密异常条目跳过（理论上不出现）
    return value or ""


def read_cookie_db(db_path: str, domain_filter: str = "xiaohongshu.com") -> list[dict]:
    """从指定 Cookies 库读取并解密 Cookie（指定 Profile 场景：独立调试 Chrome）。

    与 read_chrome_cookies() 的区别：不自动挑选 Profile，而是精确读取调用方指定的
    Cookies SQLite 库（如独立调试 Chrome 的 ~/.xhs-leads-profile/Default/Network/Cookies），
    用于「真实登录态校验」：目标浏览器里 web_session 是否真实有效。
    返回 [{name, value, domain, path, expires}]，expires 为 Chrome 微秒时间戳。
    依赖：cryptography（app/.venv 已装）。未授权时触发系统钥匙串授权弹窗。
    """
    if not os.path.exists(db_path):
        raise ChromeCookieReadError(f"未找到 Chrome Cookie 库: {db_path}")

    key = _get_decryption_key()

    con = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        try:
            meta_ver, = con.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            strip_integrity = int(meta_ver) >= 24
        except Exception:  # noqa: BLE001
            strip_integrity = False

        rows = con.execute(
            "SELECT host_key, name, value, encrypted_value, path, expires_utc "
            "FROM cookies WHERE host_key LIKE ? ORDER BY host_key, name",
            (f"%{domain_filter}%",),
        ).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    for host, name, value, enc_value, path, exp_utc in rows:
        blob = enc_value if isinstance(enc_value, bytes) else (
            enc_value.encode("latin1") if enc_value else b""
        )
        val = _decrypt_cookie_blob(key, blob, value, strip_integrity)
        if not val:
            continue
        out.append({
            "name": name,
            "value": val,
            "domain": host,
            "path": path,
            "expires": exp_utc,
        })
    return out


def read_chrome_cookies(domain_filter: str = "xiaohongshu.com") -> list[dict]:
    """读取 Chrome 登录态 Cookie（自动挑选最可能的 Profile），返回 [{name, value, domain, path, expires}]。

    依赖：cryptography（app/.venv 已装）。未授权时触发系统钥匙串授权弹窗。
    """
    db_path = _chrome_cookie_db_path()
    if not os.path.exists(db_path):
        raise ChromeCookieReadError("未找到 Chrome Cookie 库，请先安装并登录 Chrome")
    return read_cookie_db(db_path, domain_filter)


def cookies_to_text(cookies: list[dict]) -> str:
    """转成 Cookie 导入文本（name=value; ...），与现有导入闭环格式一致。"""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("value"))
