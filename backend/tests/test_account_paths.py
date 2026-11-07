"""账号目录规则（R-6.59）：统一到 <数据目录>/accounts，注册表存相对路径，跨平台可搬。

覆盖：
- resolve_account_dir 三种写法（相对数据目录 / ~ 展开 / 绝对原样）
- 新账号默认目录是相对路径（不是 ~ 绝对）
- default 默认目录 = accounts/default
- 账号 id 作为目录名的跨平台合法性
"""
import os

import pytest

from src import config


def test_accounts_dir_under_data_dir(monkeypatch, tmp_path):
    """统一根必须在数据目录内，跟着 DATA_DIR 走（macOS/Windows 由 launcher 注入）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert config.accounts_dir() == tmp_path / "accounts"


def test_resolve_relative_joins_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert config.resolve_account_dir("accounts/a") == str(tmp_path / "accounts" / "a")


def test_resolve_home_prefix_expanded():
    assert config.resolve_account_dir("~/x") == os.path.expanduser("~/x")


def test_resolve_absolute_kept_as_is():
    assert config.resolve_account_dir("/Applications/X") == "/Applications/X"


def test_resolve_empty_returns_empty():
    assert config.resolve_account_dir("") == ""
    assert config.resolve_account_dir(None) == ""


def test_new_account_stores_relative_path(monkeypatch, tmp_path):
    """新账号写相对路径：搬数据目录、换平台都不用改注册表。"""
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "acc.json")
    acc = config.add_account("zhubao_main")
    assert acc["user_data_dir"] == "accounts/zhubao_main"


def test_default_account_dir_is_relative(monkeypatch):
    """default 不再特例为 data/profile，默认 accounts/default。"""
    monkeypatch.delenv("XHS_PROFILE_DIR", raising=False)
    monkeypatch.setattr(config, "_read_json", lambda _p: {})
    assert config.default_user_data_dir() == "accounts/default"


@pytest.mark.parametrize("bad", ["中文名", "has space", "Upper", "a" * 33, "-lead", "a/b", "a.b", ""])
def test_invalid_account_id_rejected(bad):
    """目录名 = 账号 id：中文/空格/大写/超长/分隔符/点 一律拒绝（Windows 非法字符与大小写陷阱）。"""
    assert config.valid_account_id(bad) is False


@pytest.mark.parametrize("good", ["default", "verify_c", "acc_1a2b3c", "zhubao-main"])
def test_valid_account_id(good):
    assert config.valid_account_id(good) is True


def test_add_account_rejects_unsafe_id(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "acc.json")
    with pytest.raises(ValueError, match="账号 id 只能用"):
        config.add_account("中文账号")
