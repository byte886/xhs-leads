"""账号增删 HTTP 接口（R-6.61）：新增自动分端口，删除走回收区不直删。

覆盖：
- 新增：留空自动生成合法 id / 指定 id 成功 / 重复拒绝 / 非法 id 拒绝
- 删除：default 拒绝 / 不存在拒绝 / profile 移进 _trash（内容保留）/ 引擎在跑先关
"""
import asyncio

import pytest

from src import config, engine_pool, server


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """数据目录与注册表都指到临时目录，绝不碰真实账号。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    (tmp_path / "accounts.json").write_text(
        '{"accounts": {}, "active": "default"}', encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- 新增

def test_add_generates_id_when_blank(data_dir):
    """不给 id 时自动生成，且端口落在账号端口段、目录是相对路径。"""
    r = asyncio.run(server.accounts_add(server.AccountAddReq()))
    assert r["ok"] is True
    aid = r["account_id"]
    assert config.valid_account_id(aid)
    assert r["debug_port"] >= config.ACCOUNT_PORT_BASE
    assert r["user_data_dir"].endswith(f"accounts/{aid}")


def test_add_named_account(data_dir):
    r = asyncio.run(server.accounts_add(
        server.AccountAddReq(account_id="shop_a", role="publish")))
    assert r["ok"] is True
    assert r["account_id"] == "shop_a"
    assert r["role"] == "publish"


def test_add_rejects_duplicate(data_dir):
    asyncio.run(server.accounts_add(server.AccountAddReq(account_id="dup")))
    r = asyncio.run(server.accounts_add(server.AccountAddReq(account_id="dup")))
    assert r["ok"] is False
    assert "已存在" in r["error"]


def test_add_rejects_unsafe_id(data_dir):
    """中文/空格这类做不了跨平台目录名的 id，必须当场拒。"""
    r = asyncio.run(server.accounts_add(server.AccountAddReq(account_id="中文 名字")))
    assert r["ok"] is False
    assert "只能用" in r["error"]


# ---------------------------------------------------------------- 删除

def test_remove_refuses_default(data_dir):
    r = asyncio.run(server.accounts_remove("default"))
    assert r["ok"] is False
    assert "不可删除" in r["error"]


def test_remove_unknown(data_dir):
    r = asyncio.run(server.accounts_remove("nobody"))
    assert r["ok"] is False
    assert "不存在" in r["error"]


def test_remove_moves_profile_to_trash(data_dir):
    """profile 是移走不是删掉：原目录消失、回收区里内容还在、注册表已移除。"""
    asyncio.run(server.accounts_add(server.AccountAddReq(account_id="tmp_a")))
    prof = data_dir / "accounts" / "tmp_a"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "Cookies").write_text("x", encoding="utf-8")

    r = asyncio.run(server.accounts_remove("tmp_a"))
    assert r["ok"] is True
    assert not prof.exists()
    trash = list((data_dir / "accounts" / "_trash").iterdir())
    assert len(trash) == 1
    assert (trash[0] / "Cookies").exists()
    assert "tmp_a" not in config.list_accounts()


def test_remove_closes_running_engine(data_dir, monkeypatch):
    """引擎还开着就删账号，先关引擎再动磁盘。"""
    asyncio.run(server.accounts_add(server.AccountAddReq(account_id="tmp_b")))
    monkeypatch.setattr(engine_pool.pool, "peek", lambda _aid: object())
    closed = {"n": 0}

    async def _close(_aid):
        closed["n"] += 1
        return True

    monkeypatch.setattr(engine_pool.pool, "close", _close)
    r = asyncio.run(server.accounts_remove("tmp_b"))
    assert r["ok"] is True
    assert closed["n"] == 1
