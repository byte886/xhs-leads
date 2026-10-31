#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""激活码离线签发工具（卖家端）。

用法（在项目根运行）：
  1) 首次：生成 RSA 密钥对 + 把公钥内嵌到 license.py
     python scripts/gen_license.py --genkey
     → 私钥写入 data/license_private.pem（gitignore，勿外传）
     → 打印公钥 PEM，需粘贴进 app/src/src/license.py 的 _PUBLIC_KEY_PEM

  2) 每次给客户发码：对客户机器码签发（默认 365 天）
     python scripts/gen_license.py --issue <客户机器码> [--days 365]
     → 打印激活码，发给客户粘贴到设置页激活

原理：RSA 私钥签名（machine_code|expiry），客户端内置公钥验签。
无服务端成本、无客户端可提取密钥；激活码与机器绑定，杜绝批量分享。
未来 server 档位用同一签名结构（license.json.signature 字段），平滑演进。
"""

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
PRIV = ROOT / "data" / "license_private.pem"
PUBLIC_KEY_ANCHOR = "_PUBLIC_KEY_PEM = "


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def genkey() -> None:
    """生成 RSA-2048 密钥对：私钥落盘（勿入库），公钥打印待内嵌。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    PRIV.parent.mkdir(parents=True, exist_ok=True)
    if PRIV.exists():
        sys.exit(f"已存在 {PRIV}，勿重复生成（否则已发激活码全部失效）。如需重来请手动删除该文件。")
    PRIV.write_text(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii"),
        encoding="utf-8",
    )
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    print(f"[ok] 私钥已写入：{PRIV}（已 gitignore，勿外传）")
    print("[公钥] 请将下方内容内嵌到 app/src/src/license.py 的 _PUBLIC_KEY_PEM：")
    print(pub_pem)


def _load_priv():
    if not PRIV.exists():
        sys.exit(f"未找到私钥 {PRIV}。请先运行 --genkey 生成密钥对。")
    return serialization.load_pem_private_key(PRIV.read_bytes(), password=None)


def issue(machine_code: str, days: int) -> str:
    """对机器码签发激活码：blob=base64url({m,e})，sig=RSA(sha256(blob))。"""
    exp = int(time.time()) + days * 86400
    blob = _b64url(json.dumps({"m": machine_code, "e": exp}, separators=(",", ":")).encode("utf-8"))
    priv = _load_priv()
    sig = priv.sign(blob.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{blob}.{_b64url(sig)}"


def main() -> None:
    ap = argparse.ArgumentParser(description="激活码离线签发（卖家端）")
    ap.add_argument("--genkey", action="store_true", help="生成密钥对并把公钥内嵌到 license.py")
    ap.add_argument("--issue", metavar="MACHINE_CODE", help="对客户机器码签发激活码")
    ap.add_argument("--days", type=int, default=365, help="有效天数（默认 365）")
    ap.add_argument("--show-priv-expiry", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.genkey:
        genkey()
        return
    if args.issue:
        code = issue(args.issue.strip(), args.days)
        exp = datetime.fromtimestamp(int(time.time()) + args.days * 86400, tz=timezone.utc).astimezone()
        print(f"[激活码]（有效期至 {exp:%Y-%m-%d}，与机器码 {args.issue} 绑定）")
        print(code)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
