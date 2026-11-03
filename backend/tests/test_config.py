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
