"""save_dialog 单元测试：应用内保存位置对话框的后端能力。"""
from pathlib import Path

import pytest

from src import save_dialog


def test_places_只返回真实存在的目录(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Desktop").mkdir()
    out = save_dialog.places()
    assert out["downloads"] == str(tmp_path / "Downloads")
    assert out["desktop"] == str(tmp_path / "Desktop")
    assert "documents" not in out


def test_places_包含上次用过的目录(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    last = tmp_path / "out"
    last.mkdir()
    target = save_dialog.write_bytes(str(last / "a.csv"), b"1")
    assert target.exists()
    assert save_dialog.places()["last"] == str(last)


def test_list_dirs_返回子目录并跳过隐藏目录(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_bytes(b"x")
    out = save_dialog.list_dirs(str(tmp_path))
    assert [d["name"] for d in out["dirs"]] == ["a", "B"]
    assert out["parent"] == str(tmp_path.parent)


def test_list_dirs_根目录无上级():
    out = save_dialog.list_dirs("/")
    assert out["parent"] is None


def test_list_dirs_非目录报错(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="不是文件夹"):
        save_dialog.list_dirs(str(f))


def test_write_bytes_正常写入并建父目录(tmp_path):
    target = save_dialog.write_bytes(str(tmp_path / "new" / "a.json"), b"{}")
    assert target.read_bytes() == b"{}"


def test_write_bytes_目标是文件夹时报错(tmp_path):
    with pytest.raises(ValueError, match="请选择具体文件名"):
        save_dialog.write_bytes(str(tmp_path), b"x")


def test_write_bytes_写入失败包装成中文错误(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(ValueError, match="写入失败"):
        save_dialog.write_bytes(str(tmp_path / "a.csv"), b"x")
