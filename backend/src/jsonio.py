"""本地 JSON 文件读写工具（跨模块复用，消除重复的 try/read/{} 模式）。

背景：G1 gauntlet 质检发现 llm_service._load 与 server._load_persona 为完全相同的
读 JSON 兜底模式（DRY 重复候选 score 1.0）——抽到此模块统一。
"""
import json
from pathlib import Path


def read_json_safe(path: Path, default: dict | None = None) -> dict:
    """读取 JSON 文件，解析失败/不存在时返回 default（默认 {}）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf8"))
    except Exception:  # noqa: BLE001
        return {} if default is None else default


def write_json(path: Path, data: dict) -> None:
    """写 JSON 文件（ensure_ascii=False + indent=2，本地明文配置统一格式）。"""
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
