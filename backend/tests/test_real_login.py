"""真实登录校验（采集/搜索前置，防空跑）单测。

背景：纸面登录态（本地 cookies.json 副本）≠ 目标调试 Chrome 真实会话有效。
本模块校验：_target_chrome_cookie_db 路径判定 + _require_real_login 三层链路
（目标 Chrome → 日常 Chrome 自动恢复 → 拦截）+ _require_login 双重熔断。
"""
import asyncio
import os

import pytest
from fastapi import HTTPException

import src
import src.browser_client  # noqa: F401  确保包属性加载（server 内为函数级 import）
import src.cookie_reader  # noqa: F401
from src import server

NOW_US = 1_800_000_000_000_000  # 远在未来，保证未过期


def _fake_cookie(name, expires=NOW_US, value="v"):
    return {"name": name, "value": value, "domain": ".xiaohongshu.com",
            "path": "/", "expires": expires}


def _patch_settings(monkeypatch, profile_dir):
    """把目标调试 Chrome 的 profile 目录指向临时目录（避免触碰真实独立 Profile）。"""
    def fake_settings():
        return 9223, str(profile_dir)
    monkeypatch.setattr(src.browser_client, "_profile_settings", fake_settings)


def _make_db(tmp_path):
    (tmp_path / "Default" / "Network").mkdir(parents=True)
    (tmp_path / "Default" / "Network" / "Cookies").write_bytes(b"x")


def _patch_common(monkeypatch, tmp_path):
    """把落盘目标指向 tmp、注入函数置空、强制 profile 模式，防测试写真实 data/。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(server, "BASE", tmp_path)
    monkeypatch.setenv("XHS_BROWSER_MODE", "profile")
    async def noop_inject():
        return False
    monkeypatch.setattr(src.browser_client, "inject_saved_cookies_now", noop_inject)


# ---------------------------------------------------------------- 路径判定

def test_target_db_prefers_network_cookies(tmp_path):
    """Chrome 156+ 路径（Default/Network/Cookies）优先。"""
    db = tmp_path / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"x")
    assert server._target_chrome_cookie_db(str(tmp_path)) == str(db)


def test_target_db_falls_back_legacy(tmp_path):
    """旧版本路径（Default/Cookies）兜底。"""
    db = tmp_path / "Default" / "Cookies"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"x")
    assert server._target_chrome_cookie_db(str(tmp_path)) == str(db)


def test_target_db_none_when_missing(tmp_path):
    """独立 Profile 未初始化 → None（走日常 Chrome 自动恢复链路）。"""
    assert server._target_chrome_cookie_db(str(tmp_path)) is None


# ---------------------------------------------------------------- 真实校验链路

def test_real_login_read_fail(monkeypatch, tmp_path):
    """目标库存在但读取失败（钥匙串未授权）→ read_fail 拦截。"""
    _make_db(tmp_path)
    _patch_settings(monkeypatch, tmp_path)
    _patch_common(monkeypatch, tmp_path)

    def boom(db_path, domain_filter="xiaohongshu.com"):
        raise RuntimeError("钥匙串未授权")
    monkeypatch.setattr(src.cookie_reader, "read_cookie_db", boom)
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is False and r["code"] == "read_fail"
    assert "钥匙串" in r["note"]


def test_real_login_ok(monkeypatch, tmp_path):
    """目标 Chrome 有有效 web_session → ok。"""
    _make_db(tmp_path)
    _patch_settings(monkeypatch, tmp_path)
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        src.cookie_reader, "read_cookie_db",
        lambda db_path, domain_filter="xiaohongshu.com":
            [_fake_cookie("web_session", expires=NOW_US)],
    )
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is True and r["code"] == "ok"


def test_real_login_restores_from_daily(monkeypatch, tmp_path):
    """目标 Chrome 无库/无有效会话 → 自动读日常 Chrome（Vicky）恢复，无需扫码。"""
    _patch_settings(monkeypatch, tmp_path)  # profile 目录不存在（db=None）
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        src.cookie_reader, "read_chrome_cookies",
        lambda domain_filter="xiaohongshu.com":
            [_fake_cookie("web_session", expires=NOW_US),
             _fake_cookie("a1", expires=NOW_US)],
    )
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is True and r["code"] == "restored"
    # 落盘 cookies.json 应包含转换后的 cookie（含注入所需字段）
    p = tmp_path / "data" / "cookies.json"
    assert p.exists()
    import json
    payload = json.loads(p.read_text(encoding="utf8"))
    assert payload["imported_at"]
    names = {c["name"] for c in payload["cookies"]}
    assert "web_session" in names
    sample = payload["cookies"][0]
    assert sample["domain"].startswith(".") and sample["sameSite"] == "Lax"


def test_real_login_no_valid_anywhere(monkeypatch, tmp_path):
    """目标 Chrome 无库 且 日常 Chrome 无有效会话 → not_logged_in 拦截。"""
    _patch_settings(monkeypatch, tmp_path)
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        src.cookie_reader, "read_chrome_cookies",
        lambda domain_filter="xiaohongshu.com": [_fake_cookie("a1", expires=1_000)],
    )
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is False and r["code"] == "not_logged_in"


def test_real_login_daily_read_fail(monkeypatch, tmp_path):
    """profile 模式：目标 Chrome 无库 且 日常 Chrome 读取异常 → daily_fail 拦截（提示授权）。"""
    _patch_settings(monkeypatch, tmp_path)
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        src.cookie_reader, "read_chrome_cookies",
        lambda domain_filter="xiaohongshu.com": (_ for _ in ()).throw(RuntimeError("钥匙串 pending")),
    )
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is False and r["code"] == "daily_fail"


def test_real_login_daily_mode_ok(monkeypatch, tmp_path):
    """非 profile 模式（连日常 Chrome 采集）：目标即日常 Chrome，其有效即 ok。"""
    _patch_settings(monkeypatch, tmp_path)  # 独立 profile 无库（不适用）
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(server, "BASE", tmp_path)
    monkeypatch.delenv("XHS_BROWSER_MODE", raising=False)
    async def noop_inject():
        return False
    monkeypatch.setattr(src.browser_client, "inject_saved_cookies_now", noop_inject)
    monkeypatch.setattr(
        src.cookie_reader, "read_chrome_cookies",
        lambda domain_filter="xiaohongshu.com": [_fake_cookie("web_session", expires=NOW_US)],
    )
    r = asyncio.run(server._require_real_login())
    assert r["ok"] is True and r["code"] == "ok"


# ---------------------------------------------------------------- _require_login 双重熔断

def test_require_login_blocks_on_real_failure(monkeypatch):
    """纸面已登录但真实会话全部失效 → 403（绝不空跑）。"""
    async def fake_login_status():
        return {"status": "logged_in", "logged_in": True}
    async def fake_real_login():
        return {"ok": False, "code": "not_logged_in",
                "note": "目标 Chrome 与日常 Chrome 均无有效小红书登录态：请扫码登录，或导入有效 Cookie 后重试"}
    monkeypatch.setattr(server, "login_status", fake_login_status)
    monkeypatch.setattr(server, "_require_real_login", fake_real_login)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._require_login())
    assert exc.value.status_code == 403
    assert "扫码登录" in exc.value.detail


def test_require_login_passes_when_real_ok(monkeypatch):
    """纸面 + 真实（含自动恢复）有效 → 放行。"""
    async def fake_login_status():
        return {"status": "logged_in", "logged_in": True}
    async def fake_real_login():
        return {"ok": True, "code": "restored", "note": "已自动恢复"}
    monkeypatch.setattr(server, "login_status", fake_login_status)
    monkeypatch.setattr(server, "_require_real_login", fake_real_login)
    asyncio.run(server._require_login())  # 不应抛异常


def test_require_login_blocks_when_paper_failed(monkeypatch):
    """纸面即未登录 → 403（原有行为保留）。"""
    async def fake_login_status():
        return {"status": "not_logged_in", "logged_in": False}
    monkeypatch.setattr(server, "login_status", fake_login_status)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._require_login())
    assert exc.value.status_code == 403
