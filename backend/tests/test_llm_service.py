"""O1 LLM 配置模块单测：掩码、保存回退、门控、路由接线。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from src import llm_service, server


@pytest.fixture()
def llm_cfg(tmp_path, monkeypatch):
    """把 llm_config.json 指向临时目录，测试间互不污染。"""
    monkeypatch.setattr(llm_service, "CONFIG_PATH", tmp_path / "llm_config.json")
    return tmp_path


def test_public_config_unconfigured(llm_cfg):
    cfg = llm_service.public_config()
    assert cfg["configured"] is False
    assert cfg["llm_api_key_masked"] == ""


def test_save_and_mask(llm_cfg):
    llm_service.save_config("sk-abcdef1234567890", "https://api.openai.com/v1", "gpt-4o-mini")
    cfg = llm_service.public_config()
    assert cfg["configured"] is True
    masked = cfg["llm_api_key_masked"]
    assert masked.startswith("sk-a") and masked.endswith("7890")
    assert "abcdef123456" not in masked
    # 明文只存文件，不返前端
    raw = llm_service._load()  # noqa: SLF001
    assert raw["llm_api_key"] == "sk-abcdef1234567890"


def test_save_keeps_old_key_when_empty(llm_cfg):
    llm_service.save_config("sk-key12345678", "https://api.openai.com/v1", "gpt-4o-mini")
    llm_service.save_config("", "https://api.deepseek.com/v1", "deepseek-chat")
    raw = llm_service._load()  # noqa: SLF001
    assert raw["llm_api_key"] == "sk-key12345678"
    assert raw["openai_base_url"] == "https://api.deepseek.com/v1"


def test_require_llm_gate(llm_cfg):
    with pytest.raises(ValueError, match="未配置 AI 模型"):
        llm_service.require_llm()
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    assert llm_service.require_llm()["default_model"] == "gpt-4o-mini"


def test_enabled_flag(llm_cfg):
    assert llm_service.enabled() is False
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    assert llm_service.enabled() is True


def test_test_with_saved_fallback(llm_cfg, monkeypatch):
    """表单 key 为空 → 回退已存配置发起测试。"""
    llm_service.save_config("sk-saved", "https://api.openai.com/v1", "gpt-4o-mini")
    called = {}

    async def fake_get(self, url, **kw):
        called["url"] = url
        assert kw["headers"]["Authorization"] == "Bearer sk-saved"
        return _FakeResp(200, {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})

    monkeypatch.setattr(llm_service.httpx.AsyncClient, "get", fake_get)
    r = asyncio.run(llm_service.test_with_saved("", "", ""))
    assert r["ok"] is True and "gpt-4o-mini" in r["models"]
    assert called["url"].endswith("/models")


def test_test_with_saved_incomplete(llm_cfg):
    r = asyncio.run(llm_service.test_with_saved("", "", ""))
    assert r["ok"] is False and "未填写完整" in r["error"]


def test_clear_config_returns_unconfigured(llm_cfg):
    """clear_config：Key/Base/模型全清，回到未配置态。"""
    llm_service.save_config("sk-abc12345", "https://api.openai.com/v1", "gpt-4o-mini")
    cfg = llm_service.clear_config()
    assert cfg["configured"] is False
    assert cfg["llm_api_key_masked"] == ""
    raw = llm_service._load()  # noqa: SLF001
    assert raw["llm_api_key"] == ""
    assert raw["openai_base_url"] == ""


def test_clear_after_save_keep_old_key_flow(llm_cfg):
    """完整链路：保存→空key保存保留→显式清除→未配置（无残留）。"""
    llm_service.save_config("sk-keepme", "https://api.openai.com/v1", "gpt-4o-mini")
    llm_service.save_config("", "https://api.deepseek.com/v1", "deepseek-chat")  # 空 key 保留旧 key
    assert llm_service._load()["llm_api_key"] == "sk-keepme"  # noqa: SLF001
    llm_service.clear_config()
    assert llm_service.enabled() is False
    assert llm_service._load()["llm_api_key"] == ""  # noqa: SLF001


# ---- 路由接线 ----
def test_routes_llm_config(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.get("/api/llm_config")
    assert r.status_code == 200 and r.json()["configured"] is False

    r = c.post("/api/llm_config", json={"api_key": "sk-abc", "openai_base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini"})
    assert r.status_code == 200 and r.json()["configured"] is True

    r = c.get("/api/llm_config")
    assert "sk-abc" not in r.json()["llm_api_key_masked"]


def test_routes_llm_config_clear(llm_cfg, monkeypatch):
    """POST /api/llm_config/clear → 未配置态；清除后保存功能仍可用。"""
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    c.post("/api/llm_config", json={"api_key": "sk-xyz", "openai_base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini"})
    r = c.post("/api/llm_config/clear")
    assert r.status_code == 200 and r.json()["configured"] is False
    assert r.json()["llm_api_key_masked"] == ""
    # 清除后可重新保存
    r2 = c.post("/api/llm_config", json={"api_key": "sk-new", "openai_base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat"})
    assert r2.status_code == 200 and r2.json()["configured"] is True


def test_routes_llm_test_unconfigured(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/llm_test", json={"api_key": "", "openai_base_url": "", "default_model": ""})
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ---- O1-2 AI 文稿生成路由 ----
def test_ai_generate_unknown_action(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "nope", "note": {}})
    assert r.status_code == 400 and "未知 AI 动作" in r.json()["detail"]


def test_ai_generate_gate_when_unconfigured(llm_cfg, monkeypatch):
    """未配置 LLM → 400（Key 门控）。"""
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 400 and "未配置 AI 模型" in r.json()["detail"]


def test_ai_generate_success(llm_cfg, monkeypatch):
    """已配置 → mock chat 返回结果。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)

    async def fake_chat(system, user, **kw):
        assert "标题" in user
        return "翡翠小白避坑指南"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["result"] == "翡翠小白避坑指南"


# ---- O1-7 AI 文稿质量保障：人设注入 + 爆文模板 ----
def test_ai_generate_persona_injected(llm_cfg, monkeypatch, tmp_path):
    """已配置人设 → system prompt 注入行业/口吻/主题。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")
    server._save_persona({"industry": "珠宝翡翠", "tone": "干货型", "topics": "避坑指南、性价比测评"})

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "body_create", "note": {"title": "翡翠怎么挑", "desc": "讲透种水"}})
    assert r.status_code == 200
    assert "行业【珠宝翡翠】" in captured["system"]
    assert "口吻【干货型】" in captured["system"]
    assert "常发主题【避坑指南、性价比测评】" in captured["system"]


def test_ai_generate_persona_empty_keeps_default(llm_cfg, monkeypatch, tmp_path):
    """未配置人设 → system prompt 保持通用助手（行为不变）。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 200
    assert "账号人设" not in captured["system"]
    assert "小红书内容创作助手" in captured["system"]


def test_ai_generate_body_prompt_has_golden_structure(llm_cfg, monkeypatch):
    """body_create 动作提示内置爆文黄金结构（质量保障模板）。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "body_create", "note": {"title": "翡翠", "desc": "种水干货"}})
    assert r.status_code == 200
    assert "黄金结构" in captured["user"]
    assert "痛点/场景开头" in captured["user"]
    assert "价值收尾" in captured["user"]


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = "fake"

    def json(self):
        return self._json


# ==================== 补充：HTTP 错误层 + 全动作覆盖（mock 闭环，不需真实 Key） ====================

class _FakePost:
    """模拟 httpx AsyncClient.post：可注入状态码/json/异常。"""

    def __init__(self, status=200, json=None, exc=None):
        self.status = status
        self._json = json if json is not None else {"choices": [{"message": {"content": "模拟正文"}}]}
        self.exc = exc
        self.called = {}

    async def __call__(self, url, **kw):
        self.called["url"] = url
        self.called["kw"] = kw
        if self.exc is not None:
            raise self.exc
        return _FakeResp(self.status, self._json)


def test_llm_chat_payload_and_success(llm_cfg, monkeypatch):
    """chat：构造 OpenAI 兼容 payload（model/messages/max_tokens/temperature）并成功解析。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    fake = _FakePost(json={"choices": [{"message": {"content": "  正文结果  "}}]})
    monkeypatch.setattr(llm_service.httpx.AsyncClient, "post", fake)
    out = asyncio.run(llm_service.chat("系统", "用户"))
    assert out == "正文结果"
    assert fake.called["url"].endswith("/chat/completions")
    payload = fake.called["kw"]["json"]
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"][0]["content"] == "系统"
    assert payload["messages"][1]["content"] == "用户"
    assert payload["max_tokens"] == 1200 and payload["temperature"] == 0.7


def test_llm_chat_http_error_raises(llm_cfg, monkeypatch):
    """chat：网络异常 → ValueError（上层转 400）。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    fake = _FakePost(exc=httpx_connect_error())
    monkeypatch.setattr(llm_service.httpx.AsyncClient, "post", fake)
    with pytest.raises(ValueError, match="AI 请求失败"):
        asyncio.run(llm_service.chat("s", "u"))


def test_llm_chat_non_200_raises(llm_cfg, monkeypatch):
    """chat：非 200（如 429 限流）→ ValueError 含 HTTP 码。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    fake = _FakePost(status=429, json={"error": {"message": "rate limited"}})
    monkeypatch.setattr(llm_service.httpx.AsyncClient, "post", fake)
    with pytest.raises(ValueError, match="HTTP 429"):
        asyncio.run(llm_service.chat("s", "u"))


def test_llm_chat_bad_response_format(llm_cfg, monkeypatch):
    """chat：200 但响应缺 choices → ValueError 提示格式异常。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    fake = _FakePost(status=200, json={"foo": "bar"})
    monkeypatch.setattr(llm_service.httpx.AsyncClient, "post", fake)
    with pytest.raises(ValueError, match="格式异常"):
        asyncio.run(llm_service.chat("s", "u"))


def test_connection_http_error(llm_cfg, monkeypatch):
    """test_connection：网络异常 → ok=False 且提示连接失败。"""
    async def fake_get(self, url, **kw):
        raise httpx_connect_error()

    monkeypatch.setattr(llm_service.httpx.AsyncClient, "get", fake_get)
    r = asyncio.run(llm_service.test_connection("https://api.openai.com/v1", "sk-k", "gpt-4o-mini"))
    assert r["ok"] is False and "连接失败" in r["error"]


def test_connection_non_200(llm_cfg, monkeypatch):
    """test_connection：401 → ok=False 且带 HTTP 码。"""
    async def fake_get(self, url, **kw):
        return _FakeResp(401, {"error": "unauthorized"})

    monkeypatch.setattr(llm_service.httpx.AsyncClient, "get", fake_get)
    r = asyncio.run(llm_service.test_connection("https://api.openai.com/v1", "sk-bad", "gpt-4o-mini"))
    assert r["ok"] is False and "401" in r["error"]


def test_ai_generate_remaining_actions(llm_cfg, monkeypatch):
    """未测的 title_summarize / body_rewrite / tags 成功路径 + prompt 参数注入。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    captured = {}

    async def fake_chat(system, user, **kw):
        captured["user"] = user
        return "生成结果"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)

    r = c.post("/api/ai_generate", json={"action": "title_summarize", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 200 and r.json()["result"] == "生成结果"
    assert "标题" in captured["user"] and "翡翠怎么挑" in captured["user"]

    r = c.post("/api/ai_generate", json={"action": "body_rewrite", "note": {"desc": "原正文内容"}})
    assert r.status_code == 200
    assert "原正文" in captured["user"] and "原正文内容" in captured["user"]

    r = c.post("/api/ai_generate", json={"action": "tags", "note": {"title": "翡翠", "desc": "干货", "keyword": "珠宝"}})
    assert r.status_code == 200
    assert "珠宝" in captured["user"] and "标签" in captured["user"]


def test_ai_generate_truncation(llm_cfg, monkeypatch):
    """超长 title/desc 在拼 prompt 前截断（title≤500 / desc≤2000），避免注入超限。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    captured = {}

    async def fake_chat(system, user, **kw):
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "玉" * 600}})
    assert r.status_code == 200
    # 截断后 title 片段最长 500 个「玉」
    assert "玉" * 501 not in captured["user"]
    assert captured["user"].count("玉") <= 500


def httpx_connect_error():
    import httpx
    return httpx.ConnectError("mock connect failed")


_ = _FakePost  # 供未来扩展引用（避免未使用告警）
