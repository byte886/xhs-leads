"""O1 账号人设配置接口测试（GET/POST /api/persona，本地 JSON 存储）。"""
import json

import pytest

from src import server


@pytest.fixture(autouse=True)
def _isolate_persona(tmp_path, monkeypatch):
    """人设 JSON 落 tmp_path，避免读写真实 data/persona.json。"""
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")
    yield


def test_persona_empty_default():
    """未配置时返回空串与 ok=True。"""
    r = server.get_persona()
    assert r["ok"] is True
    assert r["industry"] == ""
    assert r["tone"] == ""
    assert r["topics"] == ""
    assert r["updated_at"] == ""


def test_persona_save_and_get():
    """POST 保存后 GET 能读回完整值。"""
    out = server.set_persona(server.PersonaReq(
        industry="珠宝 · 翡翠 / 手串 / 陶瓷",
        tone="干货型 · 第一人称 · 朴实可信",
        topics="翡翠挑选避坑 / 手串盘玩 / 玉器鉴别",
    ))
    assert out["ok"] is True
    r = server.get_persona()
    assert r["industry"] == "珠宝 · 翡翠 / 手串 / 陶瓷"
    assert r["tone"] == "干货型 · 第一人称 · 朴实可信"
    assert r["topics"] == "翡翠挑选避坑 / 手串盘玩 / 玉器鉴别"
    assert r["updated_at"]


def test_persona_blank_keeps_old():
    """留空字段保留原值（前端可不重复提交）。"""
    server.set_persona(server.PersonaReq(industry="行业A", tone="口吻A", topics="主题A"))
    out = server.set_persona(server.PersonaReq(industry="行业B", tone="", topics=""))
    assert out["industry"] == "行业B"
    assert out["tone"] == "口吻A"   # 空串 → 保留旧值
    assert out["topics"] == "主题A"


def test_persona_file_written():
    """POST 后 JSON 文件确实落盘（幂等可读）。"""
    server.set_persona(server.PersonaReq(industry="I", tone="T", topics="P"))
    raw = json.loads(server.PERSONA_PATH.read_text(encoding="utf8"))
    assert raw["industry"] == "I"
    assert raw["tone"] == "T"
    assert raw["topics"] == "P"
