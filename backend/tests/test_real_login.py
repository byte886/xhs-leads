"""真实登录校验（采集/搜索前置，防空跑）单测。

R-6.34 收敛：方案 A「读系统/目标 Chrome Cookie 库自动续命」旧链路
（_target_chrome_cookie_db / _check_target_or_daily / _try_restore_daily /
cookie_reader 模块）已全部删除；_require_real_login 现仅走引擎 CDP 单链路，
由 test_server_routes.py fixture 与本模块 _require_login 双重熔断用例覆盖。
"""
import asyncio

import pytest
from fastapi import HTTPException

from src import server


# ---------------------------------------------------------------- _require_login 双重熔断

def test_require_login_blocks_on_real_failure(monkeypatch):
    """纸面已登录但真实会话全部失效 → 403（绝不空跑）。"""
    async def fake_login_status():
        return {"status": "logged_in", "logged_in": True}

    async def fake_real_login():
        return {"ok": False, "code": "not_logged_in",
                "note": "独立 profile 无有效小红书登录态：请在 app 内登录小窗完成登录"}

    monkeypatch.setattr(server, "login_status", fake_login_status)
    monkeypatch.setattr(server, "_require_real_login", fake_real_login)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._require_login())
    assert exc.value.status_code == 403
    assert "登录" in exc.value.detail


def test_require_login_passes_when_real_ok(monkeypatch):
    """纸面 + 真实有效 → 放行。"""
    async def fake_login_status():
        return {"status": "logged_in", "logged_in": True}

    async def fake_real_login():
        return {"ok": True, "code": "ok", "note": "引擎独立 profile 真实登录有效（CDP id_token）"}

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
