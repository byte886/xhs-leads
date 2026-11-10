"""行业 / 口吻选项库的存储规则。

重点不是 CRUD 本身，而是三条容易被忽略的边界：
  - 文件不存在时回落到默认集（首次启动不会拿到空下拉）
  - 整组被清空时也回落（用户全删 ≠ 让下拉变空）
  - 重复项与超长项要清理，否则下拉里会出现两个「翡翠」
"""
import pytest

from src import config, options_store


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_no_file_falls_back_to_defaults(data_dir):
    got = options_store.load()
    assert got["industries"] == options_store.DEFAULT_INDUSTRIES
    assert got["tones"] == options_store.DEFAULT_TONES


def test_save_only_industries_keeps_tones(data_dir):
    options_store.save({"industries": ["翡翠", "和田玉"]})
    got = options_store.load()
    assert got["industries"] == ["翡翠", "和田玉"]
    assert got["tones"] == options_store.DEFAULT_TONES


def test_dedupe_and_trim(data_dir):
    options_store.save({"industries": ["  翡翠 ", "翡翠", "", "黄金"]})
    assert options_store.load()["industries"] == ["翡翠", "黄金"]


def test_long_value_truncated(data_dir):
    options_store.save({"tones": ["很长" * 20]})
    assert len(options_store.load()["tones"][0]) == options_store._MAX_LEN


def test_empty_list_falls_back_not_empty(data_dir):
    """全删光时不能给前端一个空下拉——回落默认，避免用户以为坏了。"""
    options_store.save({"industries": []})
    assert options_store.load()["industries"] == options_store.DEFAULT_INDUSTRIES


def test_reset_restores_defaults(data_dir):
    options_store.save({"industries": ["只留一个"]})
    got = options_store.reset()
    assert got["industries"] == options_store.DEFAULT_INDUSTRIES
    assert got["tones"] == options_store.DEFAULT_TONES


def test_written_file_is_utf8_json(data_dir):
    options_store.save({"industries": ["翡翠"]})
    text = (data_dir / "ai_options.json").read_text(encoding="utf-8")
    assert "翡翠" in text
