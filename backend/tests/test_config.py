"""config.py 特征测试（R-6.36b：路径规范化/配置合并/端口策略现行行为固化）。"""
import os

import pytest

from src import config


# ---------------------------------------------------------------- path helpers

def test_home_relative_home_root():
    assert config.home_relative(os.path.expanduser("~")) == "~"


def test_home_relative_subpath():
    home = os.path.expanduser("~")
    assert config.home_relative(home + "/a/b") == "~/a/b"


def test_home_relative_other_absolute_kept():
    assert config.home_relative("/Applications/X") == "/Applications/X"


def test_home_relative_empty():
    assert config.home_relative("") == ""


def test_expand_none():
    assert config.expand(None) == ""


def test_expand_tilde():
    assert config.expand("~/x") == os.path.expanduser("~/x")


# ---------------------------------------------------------------- config.json merge

def test_get_config_defaults_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "none.json")
    cfg = config.get_config()
    assert cfg["engine_port"] == 9223 and cfg["front_port"] == 8791
    assert cfg["browser_mode"] == "engine"


def test_set_config_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "c.json"
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    cfg = config.set_config("request_interval", [3.0, 5.0])
    assert cfg["request_interval"] == [3.0, 5.0]
    # 持久化：重新读回
    assert config.get_config()["request_interval"] == [3.0, 5.0]


# ---------------------------------------------------------------- port policy

def test_find_free_port_pick_preferred_when_free(monkeypatch):
    monkeypatch.setattr(config, "_port_free", lambda p: True)
    assert config.find_free_port(9223) == 9223


def test_find_free_port_scans_range_when_preferred_busy(monkeypatch):
    busy = {9223, 8800, 8801}
    monkeypatch.setattr(config, "_port_free", lambda p: p not in busy)
    assert config.find_free_port(9223, lo=8800, hi=8805) == 8802


def test_find_free_port_returns_preferred_when_all_busy(monkeypatch):
    monkeypatch.setattr(config, "_port_free", lambda p: False)
    assert config.find_free_port(9223, lo=8800, hi=8802) == 9223


# ---------------------------------------------------------------- _read_json

def test_read_json_missing_returns_empty(tmp_path):
    assert config._read_json(tmp_path / "nope.json") == {}


def test_read_json_corrupted_returns_empty(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{x", encoding="utf-8")
    assert config._read_json(f) == {}


# ------------------------------------------------ engine_port / front_port / mode

def test_engine_port_env_override(monkeypatch):
    monkeypatch.setenv("XHS_PROFILE_PORT", "9333")
    assert config.engine_port() == 9333


def test_engine_port_env_bad_falls_through(monkeypatch):
    monkeypatch.setenv("XHS_PROFILE_PORT", "notaport")
    monkeypatch.delenv("XHS_SHELL_URL", raising=False)
    # 回退到默认值
    assert isinstance(config.engine_port(), int)


def test_mode_dev(monkeypatch):
    monkeypatch.setattr(config, "_MODE_ENV", "dev")
    assert config.is_dev() is True
    assert config.is_prod() is False


def test_mode_prod(monkeypatch):
    monkeypatch.setattr(config, "_MODE_ENV", "prod")
    assert config.is_prod() is True


# ------------------------------------------------ accounts 系列

def test_accounts_crud(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "acc.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert config.list_accounts() == {}
    config.add_account("acc1", role="collector")
    assert "acc1" in config.list_accounts()
    assert config.get_account("acc1")["role"] == "collector"
    config.update_account("acc1", role="publisher")
    assert config.get_account("acc1")["role"] == "publisher"
    assert config.set_active("acc1") is True
    assert config.active_account() == "acc1"
    assert config.remove_account("default") is False  # default 不可删
    assert config.remove_account("acc1") is True
    assert "acc1" not in config.list_accounts()


def test_new_account_id_unique(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "acc.json")
    aid = config.new_account_id("acc")
    assert aid.startswith("acc_")
