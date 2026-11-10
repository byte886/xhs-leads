"""账号档案存储（图文生成的「事实底座」）。

字段参考门店视频工具「基础身份 + 经营卖点」，但只保留图文会真实消费的项：
  - 基础身份：档案名 / 昵称 / 品牌门店 / 行业
  - 经营卖点：目标受众 / 核心卖点 / 产品与价格 / 门店地址
  - 表达风格：人设口吻 / 避雷词
存储：<运行数据目录>/profiles.json，明文、与账号 profile 同信任域。
"""
from __future__ import annotations

import time
import uuid

from src import config
from .jsonio import read_json_safe, write_json

STORE_PATH = config.DATA_DIR / "profiles.json"

FIELDS = (
    "name", "nickname", "brand", "industry",
    "audience", "selling_points", "products", "address",
    "tone", "avoid_words",
)


def _load() -> dict:
    d = read_json_safe(STORE_PATH)
    if not isinstance(d.get("items"), list):
        d["items"] = []
    return d


def _save(d: dict) -> dict:
    write_json(STORE_PATH, d)
    return d


def _clean(item: dict) -> dict:
    out = {k: str(item.get(k, "") or "").strip() for k in FIELDS}
    out["id"] = str(item.get("id") or "") or uuid.uuid4().hex[:10]
    out["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
    return out


def list_profiles() -> list[dict]:
    return _load()["items"]


def get_profile(profile_id: str) -> dict | None:
    for p in list_profiles():
        if p.get("id") == profile_id:
            return p
    return None


def save_profile(item: dict) -> dict:
    """按 id 新增或更新；没有 id 视为新建。档案名为空时用昵称兜底。"""
    d = _load()
    row = _clean(item)
    if not row["name"]:
        row["name"] = row["nickname"] or row["brand"] or "未命名档案"
    for i, p in enumerate(d["items"]):
        if p.get("id") == row["id"]:
            d["items"][i] = row
            _save(d)
            return row
    d["items"].insert(0, row)
    _save(d)
    return row


def delete_profile(profile_id: str) -> bool:
    d = _load()
    n = len(d["items"])
    d["items"] = [p for p in d["items"] if p.get("id") != profile_id]
    _save(d)
    return len(d["items"]) != n


def default_profile() -> dict | None:
    items = list_profiles()
    return items[0] if items else None


def profile_prompt_block(p: dict | None) -> str:
    """把档案转成提示词里的事实段落（越详细，生成越具体）。"""
    if not p:
        return ""
    pairs = [
        ("档案", p.get("name")),
        ("自称", p.get("nickname")),
        ("品牌门店", p.get("brand")),
        ("行业", p.get("industry")),
        ("目标受众", p.get("audience")),
        ("核心卖点", p.get("selling_points")),
        ("产品与价格", p.get("products")),
        ("门店地址", p.get("address")),
        ("人设口吻", p.get("tone")),
        ("避雷词（绝对不写）", p.get("avoid_words")),
    ]
    body = "；".join(f"{k}：{v}" for k, v in pairs if v)
    if not body:
        return ""
    return f"【账号档案】{body}。生成时只能用档案里的真实信息，不得虚构价格、地址、疗效或体验细节。"
