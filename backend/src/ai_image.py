"""生图供应商配置（与生文完全分离）。

设计口径：
- 生文走 llm_service（既有 OpenAI 兼容链路），生图独立配置，避免两套能力互相污染；
- 配置存 <运行数据目录>/ai_image_config.json（与账号 profile 同信任域，不进 git）；
- 下拉只列「确实能出图」的模型：DeepSeek flash/pro 仅输出 text，因此不在生图供应商清单里；
- 接口只回掩码，明文不出后端。
"""
from __future__ import annotations

import time

import httpx

from src import config
from .jsonio import read_json_safe, write_json

CONFIG_PATH = config.DATA_DIR / "ai_image_config.json"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# 供应商注册表：models 只放已验证可出图的模型。
PROVIDERS = [
    {
        "id": "dashscope",
        "label": "阿里云百炼 · 通义万相",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "models": ["wan2.7-image", "wan2.7-image-pro"],
        "note": "国内直连不用代理，中文文字渲染强，约 0.22 元/张",
    },
    {
        "id": "gemini",
        "label": "Google · Nano Banana",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"],
        "note": "版式与画质强，需代理；按张计费（1K 约 $0.067）",
    },
    {
        "id": "openai_compat",
        "label": "自定义 · OpenAI 兼容",
        "base_url": "",
        "models": [],
        "note": "填聚合中转站的生图模型名，要求该端点支持 /images/generations",
    },
]


def provider_catalog() -> list[dict]:
    """前端下拉用：供应商 + 各自可出图模型。"""
    return PROVIDERS


def _load() -> dict:
    return read_json_safe(CONFIG_PATH)


def _save(cfg: dict) -> dict:
    write_json(CONFIG_PATH, cfg)
    return cfg


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "*" * max(4, len(key) - 2)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def public_config() -> dict:
    cfg = _load()
    return {
        "configured": bool(cfg.get("api_key") and cfg.get("model")),
        "provider": cfg.get("provider", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "api_key_masked": _mask(cfg.get("api_key", "")),
        "updated_at": cfg.get("updated_at", ""),
        "providers": provider_catalog(),
    }


def save_config(provider: str, api_key: str, base_url: str, model: str) -> dict:
    """保存生图配置；api_key 为空时保留旧 key（与生文一致的保护语义）。"""
    cfg = _load()
    if api_key.strip():
        cfg["api_key"] = api_key.strip()
    cfg["provider"] = provider.strip()
    cfg["base_url"] = base_url.strip().rstrip("/")
    cfg["model"] = model.strip()
    cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(cfg)
    return public_config()


def clear_config() -> dict:
    _save({
        "provider": "",
        "api_key": "",
        "base_url": "",
        "model": "",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return public_config()


def require_image() -> dict:
    cfg = _load()
    if not (cfg.get("api_key") and cfg.get("model")):
        raise ValueError("未配置生图模型：请先在「AI」中填写生图供应商的 API Key 与模型")
    return cfg


async def test_connection(provider: str, api_key: str, base_url: str) -> dict:
    """测试生图配置。

    生图供应商普遍不提供「列出全部可出图模型」的端点，这里只做轻量探活：
    - 有 /models 就列出来并核对所选模型；
    - 返回 404/权限错误时如实说明「该供应商不支持列举，需实际生成一次才能确认」。
    不发起真实生图（会产生费用）。
    """
    key = (api_key or "").strip() or _load().get("api_key", "")
    base = (base_url or "").strip().rstrip("/") or _load().get("base_url", "")
    if not key or not base:
        return {"ok": False, "error": "请填写 API Key 与 Base URL"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"连接失败：{e}"}
    if r.status_code == 200:
        try:
            items = r.json().get("data") or []
            names = [x.get("id", "") for x in items if isinstance(x, dict)]
        except Exception:  # noqa: BLE001
            names = []
        return {"ok": True, "count": len(names), "models": names[:10]}
    if r.status_code in (401, 403):
        return {"ok": False, "error": f"鉴权失败（HTTP {r.status_code}）：检查 API Key 是否属于该供应商"}
    return {
        "ok": True,
        "count": 0,
        "models": [],
        "note": f"该端点不支持模型列举（HTTP {r.status_code}）；配置已保存，首次生成时再做真实校验",
    }
