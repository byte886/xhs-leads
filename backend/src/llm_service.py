"""LLM 配置与 OpenAI 兼容调用（O1 AI 模块，Key 门控）。

设计（参考 O1 设计定稿 §3.1）：
- 配置存 data/llm_config.json（本地明文，与 cookies.json 同级别信任域）
- GET /api/llm_config 不返回明文 key（掩码展示）
- 未配置时 require_llm() 抛 ValueError → 上层转 HTTP 400（前端 AI 按钮灰态+提示）
- 调用走 OpenAI 兼容 chat/completions（httpx 直连，不引 openai SDK）
"""
import json
import time
from pathlib import Path

import httpx

from .jsonio import read_json_safe, write_json

# 与 server.py 同基准：~/Desktop/xhs-leads
_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = _ROOT / "data" / "llm_config.json"
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def _load() -> dict:
    return read_json_safe(CONFIG_PATH)


def _save(cfg: dict) -> dict:
    write_json(CONFIG_PATH, cfg)
    return cfg


def _mask(key: str) -> str:
    """Key 掩码：前 4 + 后 4，中间 *。短 key 整段掩码。"""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "*" * max(4, len(key) - 2)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def public_config() -> dict:
    """前端可见配置（key 掩码，不泄露明文）。"""
    cfg = _load()
    return {
        "configured": bool(cfg.get("llm_api_key") and cfg.get("openai_base_url") and cfg.get("default_model")),
        "openai_base_url": cfg.get("openai_base_url", ""),
        "default_model": cfg.get("default_model", ""),
        "llm_api_key_masked": _mask(cfg.get("llm_api_key", "")),
        "updated_at": cfg.get("updated_at", ""),
    }


def save_config(api_key: str, base_url: str, model: str) -> dict:
    """保存配置：key 为空时保留旧 key（前端可不回传），base/model 直接覆盖。"""
    cfg = _load()
    new_key = api_key.strip()
    if new_key:
        cfg["llm_api_key"] = new_key
    cfg["openai_base_url"] = base_url.strip().rstrip("/")
    cfg["default_model"] = model.strip()
    cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(cfg)
    return public_config()


def clear_config() -> dict:
    """清除 AI 配置（Key/Base/模型全清），回到未配置态。

    背景：save_config 空 key 保留是保护设计（前端不持久回显明文），
    但用户换号/换中转站后无法在 UI 移除已配置 Key——本函数提供显式清除语义。
    """
    cfg = {
        "llm_api_key": "",
        "openai_base_url": "",
        "default_model": "",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(cfg)
    return public_config()


def enabled() -> bool:
    """是否已配置（前端据此灰态/放行）。"""
    cfg = _load()
    return bool(cfg.get("llm_api_key") and cfg.get("openai_base_url") and cfg.get("default_model"))


def require_llm() -> dict:
    """门控：未配置抛 ValueError（上层转 HTTP 400）。返回完整配置。"""
    cfg = _load()
    if not (cfg.get("llm_api_key") and cfg.get("openai_base_url") and cfg.get("default_model")):
        raise ValueError("未配置 AI 模型：请先在「AI 设置」中配置 API Key / Base URL / 模型")
    return cfg


async def test_connection(base_url: str, api_key: str, model: str) -> dict:
    """测试连接：GET /models（OpenAI 兼容端点）。返回可用模型清单/错误。"""
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
            return {
                "ok": True,
                "count": len(models),
                "models": models[:10],
                "model_available": (model in models) if models else None,
                "note": "模型已列出" if models else "端点可用但未返回模型清单",
            }
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"连接失败：{e}"}


async def test_with_saved(api_key: str, base_url: str, model: str) -> dict:
    """测试连接（表单值缺省时回退已存配置）：前端可不回传 key，缺空则提示未填写完整。"""
    cfg = _load()
    api_key = api_key.strip() or cfg.get("llm_api_key", "")
    base_url = base_url.strip().rstrip("/") or cfg.get("openai_base_url", "")
    model = model.strip() or cfg.get("default_model", "")
    if not (api_key and base_url and model):
        return {"ok": False, "error": "未填写完整配置（API Key / Base URL / 模型）"}
    return await test_connection(base_url, api_key, model)


async def chat(system: str, user: str, max_tokens: int = 1200, temperature: float = 0.7) -> str:
    """调用 OpenAI 兼容 chat/completions。未配置抛 ValueError，失败同样抛 ValueError（上层转 400）。"""
    cfg = require_llm()
    url = cfg["openai_base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['llm_api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["default_model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise ValueError(f"AI 请求失败：{e}") from e
    if r.status_code != 200:
        raise ValueError(f"AI 请求失败 HTTP {r.status_code}: {r.text[:150]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise ValueError(f"AI 响应格式异常：{str(data)[:150]}") from e
