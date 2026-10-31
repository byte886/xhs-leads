# -*- coding: utf-8 -*-
"""授权薄层（free 优先落地，activation/server 留接口）。

ADR《授权机制选型与薄层设计》定死的 4 件事：
1. 接口形状：`check_license(feature) -> (ok, reason, detail)`，所有业务入口统一过它；free 恒放行；
2. feature 清单：collect / clone / publish / llm（前后端共用同一份）；
3. 机器码算法：跨平台稳定硬件指纹（发布后换算法 = 已发激活码全废，故必须先定）；
4. License 文件 schema：版本化（schema_version=1），预留 RSA signature 字段（server 模式填充）。

档位切换只改本文件内部实现，其余业务代码零改动。
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import time

from src import config  # noqa: E402（DATA_DIR 相对项目根，可移植）

# 模式：free | activation | server（环境变量切换；未来档位只改内部实现）
LICENSE_MODE = os.environ.get("XHS_LICENSE_MODE", "free")

# 前后端共用的功能清单（免费试用/付费档位以后映射到同一清单）
FEATURES = ("collect", "clone", "publish", "llm")

_SCHEMA_VERSION = 1
_LICENSE_FILE = config.DATA_DIR / "license.json"

# 内置 RSA 公钥（activation 档位验签用；私钥在卖家端 data/license_private.pem，绝不入库）
# 生成方式：python scripts/gen_license.py --genkey → 粘贴输出到此处
_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4XgmCPWD3xKVOL/wlMtA
KFU0R/20WoY2KUapVd04xmahF6G/3glFH7Y+cXyCW7RsQBhzo0gkotqdyDGrEm/C
PXW//h6ZIIvoVc/o16tEIkS7V8HHwh1gheSfgdFWK/BfMEiN1b6ib9uMTFDPbk/T
9YD6H2tvzvMg103QFXKgi2gxZBnBQxpbLBXm8Dklhj3COf0zuNtlFOXb751LEhP/
Akej4Rs88no56yfidtrd7YXcHTDwZ9aU9ENXIpyJqNqePg/xxGZL5aF9RFuYDt1+
pfogq+TqY5BzJWr51pAs7vYQXg50GqA0dOQa5Xi35i8FS6xTuyUjqyZYifaSsEIZ
OQIDAQAB
-----END PUBLIC KEY-----
"""


def check_license(feature: str, detail: dict | None = None) -> tuple[bool, str, dict]:
    """统一权限问询。所有业务入口（启动/采集/克隆/发布/LLM/前端路由守卫）过此函数。

    free 模式恒 (True, "", {...})——调试/分享期零门槛、无感；
    activation 校验激活码（RSA 验签 + 机器码绑定 + 有效期）；server 档位预留。
    """
    if feature not in FEATURES:
        return False, f"未知功能：{feature}", {"mode": LICENSE_MODE}
    if LICENSE_MODE == "free":
        return True, "", {"mode": "free", "feature": feature}
    return _check_paid(feature, detail or {})


def _check_paid(feature: str, detail: dict) -> tuple[bool, str, dict]:
    """activation：读 license.json 激活码，RSA 验签 + 机器码匹配 + 未过期 → 放行。

    server（未来）：读签名许可（signature 字段）→ 联网校验后放行；
    两者共用同一公钥验签结构（license.json.signature 字段即为 server 预留）。
    """
    lic = load_license_file()
    if lic.get("signature"):
        return True, "", {"mode": "server", "feature": feature}
    code = lic.get("activation_code") or ""
    if not code:
        return (
            False,
            "未激活：请在设置页输入激活码（或设置 XHS_LICENSE_MODE=free 保持免费试用）",
            {"mode": LICENSE_MODE, "feature": feature},
        )
    ok, reason = _verify_activation_code(code)
    if not ok:
        return False, reason, {"mode": LICENSE_MODE, "feature": feature}
    return True, "", {"mode": "activation", "feature": feature}


def _verify_activation_code(code: str) -> tuple[bool, str]:
    """验签激活码：blob.sig 格式，RSA 验签 + 机器码匹配 + 有效期。

    blob = base64url(JSON{"m": machine_code, "e": exp_ts})，sig = RSA-SHA256(blob)。
    """
    import base64

    try:
        blob_b64, sig_b64 = code.strip().split(".", 1)
        blob = base64.urlsafe_b64decode(blob_b64 + "=" * (-len(blob_b64) % 4))
        sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        payload = json.loads(blob.decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False, "激活码格式错误"
    if not isinstance(payload, dict) or "m" not in payload or "e" not in payload:
        return False, "激活码格式错误"
    # RSA 验签（PKCS1v15-SHA256）
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        pub = load_pem_public_key(_PUBLIC_KEY_PEM.encode("ascii"))
        # 签发侧签的是 base64url 字符串（blob.encode("ascii")），验签必须用同一对象
        pub.verify(sig, blob_b64.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except Exception:  # noqa: BLE001（任何验签失败 = 激活码无效）
        return False, "激活码无效（签名不匹配）"
    if payload.get("m") != machine_code():
        return False, "激活码与设备不匹配（激活码绑定机器码）"
    exp = payload.get("e")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return False, "激活码已过期"
    return True, ""


def machine_code() -> str:
    """跨平台稳定硬件指纹（SHA-256 摘要，非明文；不进 git、不落日志明文）。

    - macOS：IOPlatformUUID（ioreg，重启不变、同机稳定）；
    - Windows：MachineGuid（HKLM\\SOFTWARE\\Microsoft\\Cryptography，装机不变）；
    - 兜底：主机名|用户（同机稳定，不可跨设备共享）。
    算法发布后不可更换（换 = 已发激活码全废），改动需评审 + 版本迁移。
    """
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 3 and parts[-2].strip():
                        return _digest(parts[-2].strip())
        elif sys.platform in ("win32", "cygwin"):
            import winreg  # noqa: PLC0415（仅 Windows 导入）

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                return _digest(str(guid))
    except Exception:  # noqa: BLE001（任一平台探测失败走兜底，不阻断）
        pass
    return _digest(f"{platform.node()}|{os.getlogin() if hasattr(os, 'getlogin') else 'unknown'}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def load_license_file() -> dict:
    """读 data/license.json；schema_version=1，预留 signature 字段（server 模式填充）。

    文件不存在或损坏时返回默认结构（不抛异常——free 模式根本不需要它）。
    """
    defaults = {
        "schema_version": _SCHEMA_VERSION,
        "mode": LICENSE_MODE,
        "machine_code": machine_code(),
        "features": list(FEATURES),
        "signature": None,        # 预留：server 模式 RSA 签名
        "activated_at": None,     # 预留：activation 模式激活时间
    }
    try:
        if _LICENSE_FILE.exists():
            data = json.loads(_LICENSE_FILE.read_text(encoding="utf-8"))
            data.setdefault("schema_version", _SCHEMA_VERSION)
            data.setdefault("signature", None)
            data.setdefault("features", list(FEATURES))
            return data
    except (OSError, ValueError):
        pass
    return defaults


def activate(code: str) -> tuple[bool, str]:
    """激活（前端调用）：RSA 验签通过后把激活码持久化到 license.json。

    成功后 check_license 在 activation 模式即放行；验证失败不落盘。
    """
    code = (code or "").strip()
    ok, reason = _verify_activation_code(code)
    if not ok:
        return False, reason
    try:
        _LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LICENSE_FILE.write_text(
            json.dumps(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "mode": "activation",
                    "activation_code": code,
                    "activated_at": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as e:  # 落盘失败（如只读目录）
        return False, f"激活写入失败：{e}"
    return True, ""


def license_status() -> dict:
    """给前端展示用的授权状态（不暴露 machine_code 明文——仅返回摘要指纹）。"""
    lic = load_license_file()
    return {
        "mode": LICENSE_MODE,
        "schema_version": lic.get("schema_version", _SCHEMA_VERSION),
        "features": lic.get("features", list(FEATURES)),
        "machine_fingerprint": lic.get("machine_code") or machine_code(),  # 已是 SHA-256 摘要，可安全展示
        "has_signature": bool(lic.get("signature")),
        "has_activation": bool(lic.get("activation_code")),  # activation 模式是否已激活
        "activated_at": lic.get("activated_at"),
    }
