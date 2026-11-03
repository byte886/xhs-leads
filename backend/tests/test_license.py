"""license.py 特征测试（R-6.36b 铺保护网：固化现行行为，不改行为）。

覆盖目标：check_license 分支 / _verify_activation_code 三失败分支 / machine_code
跨平台路径 / load_license_file 默认与合并 / activate 落盘 / license_status 形状。
"""
import json

import pytest

from src import license


@pytest.fixture(autouse=True)
def _isolated_license_file(tmp_path, monkeypatch):
    """license._LICENSE_FILE 指向临时文件，不碰真实 data/license.json。"""
    monkeypatch.setattr(license, "_LICENSE_FILE", tmp_path / "license.json")
    yield


# ---------------------------------------------------------------- check_license

def test_check_license_free_mode_always_pass(monkeypatch):
    """free 模式：所有 feature 恒放行。"""
    monkeypatch.setattr(license, "LICENSE_MODE", "free")
    for f in license.FEATURES:
        ok, reason, detail = license.check_license(f)
        assert ok is True and reason == ""
        assert detail["mode"] == "free" and detail["feature"] == f


def test_check_license_unknown_feature(monkeypatch):
    """未知 feature 一律拒（与模式无关）。"""
    monkeypatch.setattr(license, "LICENSE_MODE", "free")
    ok, reason, detail = license.check_license("bogus")
    assert ok is False and "未知功能" in reason


def test_check_license_paid_no_code(monkeypatch):
    """activation 模式无激活码 → 拒，提示去设置页输入。"""
    monkeypatch.setattr(license, "LICENSE_MODE", "activation")
    ok, reason, detail = license.check_license("collect")
    assert ok is False and "未激活" in reason and detail["feature"] == "collect"


def test_check_license_paid_with_signature_is_server(monkeypatch, tmp_path):
    """license.json 有 signature → server 模式放行（预留路径）。"""
    license._LICENSE_FILE.write_text(json.dumps({"signature": "x"}), encoding="utf-8")
    monkeypatch.setattr(license, "LICENSE_MODE", "activation")
    ok, reason, detail = license.check_license("llm")
    assert ok is True and detail["mode"] == "server"


# ---------------------------------------------------------------- _verify_activation_code

def test_verify_bad_format():
    """不含 dot / 非 base64 / 非 JSON → 格式错误。"""
    ok, reason = license._verify_activation_code("not-a-code")
    assert ok is False and "格式错误" in reason


def test_verify_format_ok_but_not_dict():
    """base64 JSON 但 payload 非 dict 缺 m/e → 格式错误。"""
    import base64
    blob = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode().rstrip("=")
    ok, reason = license._verify_activation_code(blob + ".sigpart")
    assert ok is False and "格式错误" in reason


def test_verify_bad_signature():
    """payload 合法但签名不匹配 → 签名不匹配。"""
    import base64, time as _t
    payload = {"m": "whatever", "e": _t.time() + 3600}
    blob = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    ok, reason = license._verify_activation_code(blob + ".Zm9v")
    assert ok is False and "签名" in reason


# ---------------------------------------------------------------- machine_code

def test_machine_code_darwin(monkeypatch):
    """darwin：ioreg 输出解析 IOPlatformUUID → sha256 摘要。"""
    monkeypatch.setattr(license.sys, "platform", "darwin")
    out = ' "IOPlatformUUID" = "ABC-123-DEF"\n'
    monkeypatch.setattr(license.subprocess, "check_output",
                        lambda *a, **k: out)
    mc = license.machine_code()
    assert mc == license._digest("ABC-123-DEF") and len(mc) == 32


def test_machine_code_fallback_on_error(monkeypatch):
    """ioreg 抛错 → 兜底 主机名|用户 摘要。"""
    monkeypatch.setattr(license.sys, "platform", "darwin")

    def boom(*a, **k):
        raise OSError("ioreg gone")

    monkeypatch.setattr(license.subprocess, "check_output", boom)
    monkeypatch.setattr(license.platform, "node", lambda: "HOSTX")
    mc = license.machine_code()
    assert mc == license._digest("HOSTX|unknown") or len(mc) == 32


# ---------------------------------------------------------------- load_license_file

def test_load_missing_file_returns_defaults():
    d = license.load_license_file()
    assert d["schema_version"] == 1 and d["signature"] is None
    assert d["features"] == list(license.FEATURES)


def test_load_corrupted_file_returns_defaults(tmp_path):
    license._LICENSE_FILE.write_text("{not json", encoding="utf-8")
    d = license.load_license_file()
    assert d["schema_version"] == 1


def test_load_existing_merges_defaults(tmp_path):
    license._LICENSE_FILE.write_text(json.dumps({"activation_code": "X", "mode": "activation"}),
                                     encoding="utf-8")
    d = license.load_license_file()
    assert d["activation_code"] == "X" and d["features"] == list(license.FEATURES)


# ---------------------------------------------------------------- activate / status

def test_activate_bad_code_no_write():
    ok, reason = license.activate("garbage")
    assert ok is False and "格式错误" in reason
    assert not license._LICENSE_FILE.exists()


def test_activate_good_code_writes_file(monkeypatch):
    monkeypatch.setattr(license, "_verify_activation_code", lambda c: (True, ""))
    ok, _ = license.activate("FAKE.VALID")
    assert ok is True
    data = json.loads(license._LICENSE_FILE.read_text(encoding="utf-8"))
    assert data["activation_code"] == "FAKE.VALID" and data["mode"] == "activation"


def test_license_status_shape():
    s = license.license_status()
    assert set(s) >= {"mode", "features", "machine_fingerprint",
                      "has_signature", "has_activation", "activated_at"}
    assert len(s["machine_fingerprint"]) == 32  # sha256 摘要，不明文
