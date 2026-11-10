"""内容模板存储规则。

重点是 CRUD 之外的边界：
  - 无文件 / 整组清空时回落默认（前端不会拿到空列表）
  - 文字模板 name、prompt 必填，否则报错
  - 图片模板的骨架/字体/对齐非法值回落、颜色非法回落、圆角截断、布尔归一
  - reset 恢复、落盘 UTF-8
"""
import pytest

from src import config, templates_store
from src.jsonio import write_json


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_no_file_falls_back_defaults(data_dir):
    got = templates_store.load()
    assert len(got["text"]) == 5
    assert len(got["image"]) == 6


def test_save_new_text_generates_id(data_dir):
    row = templates_store.save_text({"name": "自定义", "prompt": "要求"})
    assert row["id"]
    assert len(templates_store.list_text()) == 6


def test_update_text_by_id(data_dir):
    row = templates_store.save_text({"name": "自定义", "prompt": "要求"})
    templates_store.save_text({**row, "name": "改名"})
    names = [t["name"] for t in templates_store.list_text()]
    assert "改名" in names and "自定义" not in names
    assert len(templates_store.list_text()) == 6


def test_text_requires_name_and_prompt(data_dir):
    with pytest.raises(ValueError):
        templates_store.save_text({"name": "", "prompt": "x"})
    with pytest.raises(ValueError):
        templates_store.save_text({"name": "x", "prompt": ""})


def test_delete_text(data_dir):
    assert templates_store.delete_text("dt-pitfall") is True
    assert all(t["id"] != "dt-pitfall" for t in templates_store.list_text())
    assert templates_store.delete_text("not-exist") is False


def test_image_skin_validation_and_fallback(data_dir):
    row = templates_store.save_image({
        "name": "自定义图", "skeleton": "bad",
        "skin": {"bg": "notcolor", "primary": "#123456", "font": "bad",
                 "radius": 999, "align": "bad", "number": "x", "divider": 0},
    })
    assert row["skeleton"] == "num"
    skin = row["skin"]
    assert skin["bg"] == "#f7f2ec"          # 非法颜色回落
    assert skin["primary"] == "#123456"     # 合法颜色保留
    assert skin["font"] == "sans"
    assert skin["radius"] == 28             # 超上限截断
    assert skin["align"] == "left"
    assert skin["number"] is True           # 非空字符串归一为 True
    assert skin["divider"] is False         # 0 归一为 False


def test_image_requires_name(data_dir):
    with pytest.raises(ValueError):
        templates_store.save_image({"name": "", "skeleton": "num"})


def test_reset_restores_defaults(data_dir):
    templates_store.save_text({"name": "x", "prompt": "y"})
    got = templates_store.reset()
    assert len(got["text"]) == 5
    assert len(got["image"]) == 6


def test_empty_group_falls_back(data_dir):
    write_json(data_dir / "content_templates.json", {"text": [], "image": []})
    got = templates_store.load()
    assert len(got["text"]) == 5
    assert len(got["image"]) == 6


def test_written_file_is_utf8(data_dir):
    templates_store.save_text({"name": "翡翠", "prompt": "要求"})
    text = (data_dir / "content_templates.json").read_text(encoding="utf-8")
    assert "翡翠" in text
