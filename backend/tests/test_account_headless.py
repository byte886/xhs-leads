"""按账号无头设置（R-6.58）。

实测背景：headless 曾是全局配置，切某个账号的无头会把全局改成 true，
default 下次重启就没窗口了（壳窗就是 default 引擎的窗口，
运行中关掉它 launcher 会判定应用退出，实测确实把整个进程带走了）。
所以窗口形态必须按账号独立：改 A 账号不动 B 账号，也不动全局默认值。
"""
import pytest

from src import config


@pytest.fixture
def cfg_files(tmp_path, monkeypatch):
    """账号注册表与全局配置都指向临时文件，避免污染真实数据。"""
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config._write_json(config.ACCOUNTS_FILE, {"accounts": {}, "active": "default"})
    config._write_json(config.CONFIG_FILE, {"headless": True})


def test_unspecified_account_falls_back_to_global(cfg_files):
    """从未设置过的账号继承全局默认值。"""
    config.add_account("acc_a")
    assert config.account_headless("acc_a") is True


def test_per_account_setting_overrides_global(cfg_files):
    """账号自己的设置优先，且不改全局默认值。"""
    config.add_account("acc_a")
    assert config.set_account_headless("acc_a", False) is True
    assert config.account_headless("acc_a") is False
    assert config.get_config()["headless"] is True


def test_changing_one_account_does_not_touch_others(cfg_files):
    """改 A 的无头不能牵连 B——这条是多账号窗口形态的根防线。"""
    config.add_account("acc_a")
    config.add_account("acc_b")
    config.set_account_headless("acc_a", False)
    assert config.account_headless("acc_a") is False
    assert config.account_headless("acc_b") is True


def test_default_account_can_be_set(cfg_files):
    """default 也要能记自己的形态（启动时由壳窗逻辑强制回有头）。"""
    config.add_account("default")
    assert config.set_account_headless("default", False) is True
    assert config.account_headless("default") is False


def test_unknown_account_is_rejected(cfg_files):
    """不在注册表里的账号不臆造记录。"""
    assert config.set_account_headless("ghost", True) is False
    assert "ghost" not in config.list_accounts()
