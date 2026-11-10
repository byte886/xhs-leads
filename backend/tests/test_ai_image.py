"""生图模块单测：响应解析、生成入口、供应商门控与路由（全程 mock，不发真实请求、不产生费用）。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from src import ai_image, server


def test_extract_image_urls():
    """从百炼响应里只取 type=image 的 URL，跳过文本项。"""
    data = {"output": {"choices": [{"message": {"content": [
        {"type": "text", "text": "x"},
        {"type": "image", "image": "http://a/1.png"},
        {"type": "image", "image": "http://a/2.png"},
    ]}}]}}
    assert ai_image._extract_image_urls(data) == ["http://a/1.png", "http://a/2.png"]  # noqa: SLF001


def test_generate_image_success(monkeypatch, tmp_path):
    """已配置 → 调百炼生成并下载落盘，返回本地 /app/images 路径，默认 3:4 尺寸。"""
    monkeypatch.setattr(ai_image, "CONFIG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(ai_image, "IMAGES_DIR", tmp_path / "images")
    ai_image._save({  # noqa: SLF001
        "provider": "dashscope", "api_key": "sk-x",
        "base_url": "https://dashscope.aliyuncs.com/api/v1", "model": "wan2.7-image",
    })

    async def fake_post(cfg, prompt, size, n):
        assert size == ai_image.PORTRAIT_34 and n == 1 and prompt == "提示"
        return {"output": {"choices": [{"message": {"content":
            [{"type": "image", "image": "http://a/1.png"}]}}]}}

    async def fake_download(url, filename):
        assert url == "http://a/1.png" and filename.startswith("ai_") and filename.endswith(".png")
        return f"/app/images/{filename}"

    monkeypatch.setattr(ai_image, "_dashscope_generate", fake_post)
    monkeypatch.setattr(ai_image, "_download", fake_download)
    out = asyncio.run(ai_image.generate_image("提示"))
    assert len(out) == 1 and out[0].startswith("/app/images/ai_") and out[0].endswith(".png")


def test_generate_image_not_configured(monkeypatch, tmp_path):
    """未配置 Key/模型 → require_image 抛 ValueError。"""
    monkeypatch.setattr(ai_image, "CONFIG_PATH", tmp_path / "cfg.json")
    with pytest.raises(ValueError):
        asyncio.run(ai_image.generate_image("x"))


def test_generate_image_unsupported_provider(monkeypatch, tmp_path):
    """非百炼供应商 → 明确提示尚未接入。"""
    monkeypatch.setattr(ai_image, "CONFIG_PATH", tmp_path / "cfg.json")
    ai_image._save({"provider": "gemini", "api_key": "k", "base_url": "x", "model": "m"})  # noqa: SLF001
    with pytest.raises(ValueError, match="尚未接入"):
        asyncio.run(ai_image.generate_image("x"))


def test_generate_route(monkeypatch, tmp_path):
    """路由层：mock generate_image，返回 urls。"""
    monkeypatch.setattr(ai_image, "CONFIG_PATH", tmp_path / "cfg.json")

    async def fake_gen(prompt, size=None, n=1):
        return ["http://a/1.png"]

    monkeypatch.setattr(ai_image, "generate_image", fake_gen)
    monkeypatch.setattr(server, "ai_image", ai_image)
    r = TestClient(server.app).post("/api/ai_image_generate", json={"prompt": "提示"})
    assert r.status_code == 200 and r.json()["urls"] == ["http://a/1.png"]


def test_generate_route_empty_prompt(monkeypatch, tmp_path):
    """空提示词 → 400。"""
    monkeypatch.setattr(ai_image, "CONFIG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(server, "ai_image", ai_image)
    r = TestClient(server.app).post("/api/ai_image_generate", json={"prompt": "  "})
    assert r.status_code == 400


def test_download_writes_file(monkeypatch, tmp_path):
    """下载函数：把响应字节写入 IMAGES_DIR，返回 /app/images 路径。"""
    monkeypatch.setattr(ai_image, "IMAGES_DIR", tmp_path / "images")

    class FakeResp:
        status_code = 200
        content = b"PNGDATA"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(ai_image.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    path = asyncio.run(ai_image._download("http://a/1.png", "ai_x_0.png"))  # noqa: SLF001
    assert path == "/app/images/ai_x_0.png"
    assert (tmp_path / "images" / "ai_x_0.png").read_bytes() == b"PNGDATA"
