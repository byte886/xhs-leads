"""窗口位置记忆（R-6.85）：记录优先级、钳制与原子落盘。

对应需求「记住每次启动的最后位置，下次启动沿用；没有记录时用默认左右分屏」。
"""
import json

import pytest

from src import config, window_layout


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_load_missing_file_returns_empty(data_dir):
    assert window_layout.load() == {}


def test_load_corrupt_file_returns_empty(data_dir):
    window_layout.path().write_text("{not json", encoding="utf-8")
    assert window_layout.load() == {}


def test_save_then_load_roundtrip(data_dir):
    assert window_layout.save("shell", {"left": 0, "top": 25, "width": 960, "height": 1140})
    assert window_layout.load()["shell"] == {
        "left": 0, "top": 25, "width": 960, "height": 1140,
    }
    # 落盘内容必须是合法 JSON（写一半被 kill 时不留下损坏文件）
    json.loads(window_layout.path().read_text(encoding="utf-8"))


def test_save_unchanged_does_not_write(data_dir):
    b = {"left": 960, "top": 25, "width": 960, "height": 1140}
    assert window_layout.save("collect", b) is True
    mtime = window_layout.path().stat().st_mtime_ns
    assert window_layout.save("collect", dict(b)) is False
    assert window_layout.path().stat().st_mtime_ns == mtime


def test_save_ignores_unknown_role(data_dir):
    assert window_layout.save("window", {"left": 1, "top": 1, "width": 800, "height": 600}) is False
    assert window_layout.load() == {}


def test_save_keeps_other_role(data_dir):
    window_layout.save("shell", {"left": 10, "top": 0, "width": 960, "height": 1140})
    window_layout.save("collect", {"left": 970, "top": 0, "width": 960, "height": 1140})
    cur = window_layout.load()
    assert cur["shell"]["left"] == 10
    assert cur["collect"]["left"] == 970


@pytest.mark.parametrize("bad", [
    {"left": -4000, "top": 0, "width": 960, "height": 1140},  # 屏外残留
    {"left": 0, "top": -100, "width": 960, "height": 1140},
    {"left": "abc", "top": 0, "width": 960, "height": 1140},
    {"left": 0, "top": 0, "width": 960},
    {},
    None,
])
def test_clamp_rejects_invalid(bad):
    assert window_layout.clamp(bad, 1920, 1200) is None


def test_clamp_raises_min_size_and_caps_to_screen():
    small = window_layout.clamp({"left": 0, "top": 0, "width": 200, "height": 100}, 1920, 1200)
    assert (small["width"], small["height"]) == (window_layout.MIN_W, window_layout.MIN_H)
    big = window_layout.clamp({"left": 0, "top": 0, "width": 4000, "height": 3000}, 1920, 1200)
    assert (big["width"], big["height"]) == (1920, 1200)


def test_clamp_keeps_whole_window_on_screen():
    # 记录把窗口拖到右下屏外：整窗回拉到刚好贴边（旧版只留 320/200 可见，会裁掉内容）
    far = window_layout.clamp({"left": 3000, "top": 2000, "width": 960, "height": 1140}, 1920, 1200)
    assert far["left"] == 1920 - 960
    assert far["top"] == 1200 - 1140
    # 回归：右缘/下缘溢出（Windows 1280×800 上记录 960 宽@left925、1140 高@top75）
    win = window_layout.clamp({"left": 925, "top": 75, "width": 960, "height": 1140}, 1280, 800)
    assert win["left"] + win["width"] <= 1280
    assert win["top"] + win["height"] <= 800
    assert win["left"] >= 0 and win["top"] >= 0


def test_bounds_for_uses_default_when_no_record(data_dir):
    default = {"left": 0, "top": 0, "width": 960, "height": 1140}
    assert window_layout.bounds_for("shell", default, 1920, 1200) == default


def test_bounds_for_prefers_record(data_dir):
    window_layout.save("collect", {"left": 1000, "top": 40, "width": 900, "height": 1100})
    default = {"left": 960, "top": 0, "width": 960, "height": 1140}
    got = window_layout.bounds_for("collect", default, 1920, 1200)
    assert got == {"left": 1000, "top": 40, "width": 900, "height": 1100}


def test_bounds_for_falls_back_when_record_invalid(data_dir):
    window_layout.save("shell", {"left": -4000, "top": 0, "width": 960, "height": 1140})
    default = {"left": 0, "top": 0, "width": 960, "height": 1140}
    assert window_layout.bounds_for("shell", default, 1920, 1200) == default
