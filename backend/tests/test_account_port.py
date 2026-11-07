"""多账号调试端口分配（R-6.57）。

实测背景：多账号共用同一 --remote-debugging-port 时，Chromium 绑定失败静默跳过，
第二个账号的引擎照样起来但 CDP 不可达，测试连到的永远是先启动的那个账号。
故每账号必须独占端口，且分配结果要持久化（重启后不漂移）。
"""
import pytest

from src import config


@pytest.fixture
def accounts_file(tmp_path, monkeypatch):
    """账号注册表指向临时文件，避免污染真实 accounts.json。"""
    f = tmp_path / "accounts.json"
    monkeypatch.setattr(config, "ACCOUNTS_FILE", f)
    config._write_json(f, {"accounts": {}, "active": "default"})
    return f


def test_default_uses_engine_port(accounts_file):
    """default 账号沿用 engine_port（36 个既有 UI 测试硬编码 9223）。"""
    assert config.account_debug_port("default") == config.engine_port()


def test_new_account_gets_first_free_port(accounts_file):
    """new 账号从 9224 起分配。"""
    config.add_account("test_a")
    assert config.account_debug_port("test_a") == config.ACCOUNT_PORT_BASE


def test_two_accounts_never_share_port(accounts_file):
    """两个账号必须拿到不同端口——这条是串号的根防线。"""
    config.add_account("test_a")
    config.add_account("test_b")
    a = config.account_debug_port("test_a")
    b = config.account_debug_port("test_b")
    assert a == 9224
    assert b == 9225
    assert a != b


def test_port_is_persisted_and_stable(accounts_file):
    """重复调用返回同一端口（不漂移），且写回 accounts.json。"""
    config.add_account("test_a")
    first = config.account_debug_port("test_a")
    second = config.account_debug_port("test_a")
    assert first == second
    assert config.list_accounts()["test_a"]["debug_port"] == first


def test_default_not_allocated_from_account_pool(accounts_file):
    """default 占 9223，账号池从 9224 起，两者不重叠。"""
    assert config.account_debug_port("default") == 9223
    config.add_account("test_a")
    assert config.account_debug_port("test_a") == config.ACCOUNT_PORT_BASE
    assert config.ACCOUNT_PORT_BASE > 9223


def test_conflicting_saved_port_reallocated(accounts_file):
    """两个账号被手工写成同一端口时，后来者重新分配，不串号。"""
    config.add_account("test_a")
    config.add_account("test_b")
    config.update_account("test_a", debug_port=9224)
    config.update_account("test_b", debug_port=9224)
    a = config.account_debug_port("test_a")
    b = config.account_debug_port("test_b")
    # 冲突时两者都会被判定需重新分配；关键是最终不重复，谁保留 9224 不重要
    assert a != b
    assert {a, b} <= set(range(config.ACCOUNT_PORT_BASE, config.ACCOUNT_PORT_MAX + 1))


def test_port_map_covers_all_accounts(accounts_file):
    """account_port_map 必须包含 default 与所有已登记账号。"""
    config.add_account("test_a")
    config.add_account("test_b")
    m = config.account_port_map()
    assert m["default"] == 9223
    assert set(m.keys()) == {"default", "test_a", "test_b"}
    assert len(set(m.values())) == 3


def test_saved_port_outside_range_reallocated(accounts_file):
    """存量账号里写着越界端口（历史脏数据）时重新分配。"""
    config.add_account("test_a")
    config.update_account("test_a", debug_port=1234)
    assert config.account_debug_port("test_a") == config.ACCOUNT_PORT_BASE
