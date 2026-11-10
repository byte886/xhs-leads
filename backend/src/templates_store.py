"""内容模板存储（文字模板 + 图片模板，图文生成的「怎么说 / 长什么样」）。

两类模板在同一篇笔记里接力，不是二选一：
  - 文字模板：内容结构，给 AI 的写作要求（prompt），决定「怎么写」。
  - 图片模板：视觉版式，选骨架（skeleton）+ 调皮肤（skin），决定「长什么样」。
默认值与前端 TemplatePanel.vue 保持一致，后端独立可用；用户可增删改、一键恢复。
存储：<运行数据目录>/content_templates.json，明文、与档案同信任域。
"""
from __future__ import annotations

import re
import uuid

from src import config
from .jsonio import read_json_safe, write_json

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SKELETON_IDS = ("num", "list", "card", "compare", "split", "quote")
FONT_IDS = ("serif", "sans", "round")
ALIGN_IDS = ("left", "center")
_MAX_ITEMS = 80
_MAX_NAME = 40
_MAX_SHORT = 120


def _path():
    """运行时解析，测试里 monkeypatch config.DATA_DIR 才能生效。"""
    return config.DATA_DIR / "content_templates.json"


# ---------- 默认文字模板（5，与前端一致） ----------
DEFAULT_TEXT = [
    {"id": "dt-pitfall", "name": "避坑清单", "scene": "建立信任 / 新手",
     "desc": "先列 3 个常见坑，再给判断方法",
     "prompt": "以店主第一人称，开头点出新手最容易踩的 3 个坑；每个坑先讲错误做法、再讲为什么错、最后给正确判断方法。语气朴实不夸大，不使用“宝子们/家人们”。结尾用档案里的真实卖点和到店方式自然收束，不硬推销。"},
    {"id": "dt-compare", "name": "对比测评", "scene": "测评 / 决策",
     "desc": "两档实物对比，用参数价差说话",
     "prompt": "选两档实物做横向对比，从参数、价差、上手感、适合人群逐项对照，用具体事实和数字说话，不贬低同行。最后给出不同预算分别该怎么选的明确建议。"},
    {"id": "dt-single", "name": "单品深讲", "scene": "高客单 / 种草",
     "desc": "一件从头讲到尾，适合高客单",
     "prompt": "围绕一件产品从头讲到尾：外观、材质/种水、适合谁、佩戴场景、价格与服务。多用真实细节，不堆砌形容词；所有信息只能来自档案，不得虚构价格、地址或体验。"},
    {"id": "dt-store", "name": "门店实拍", "scene": "真实感 / 到店",
     "desc": "真实货架与上手细节，弱化模板感",
     "prompt": "以真实门店、货架、上手细节为主，文字只做简短引导，像随手记录。介绍在店里能看到什么、能体验什么，弱化推销感，自然邀请到店看货。"},
    {"id": "dt-new", "name": "上新到货", "scene": "成交 / 上新",
     "desc": "新货 + 数量 + 到店看货",
     "prompt": "讲清新到什么货、数量、价位区间和到店方式，信息简洁直接，突出真实与稀缺；不使用“保值升值、开光、招财”等词，不制造焦虑。"},
]

# ---------- 默认图片模板（6，与前端一致） ----------
DEFAULT_IMAGE = [
    {"id": "di-num", "name": "大数字·暖白", "skeleton": "num",
     "skin": {"bg": "#f7f2ec", "primary": "#e22a43", "font": "sans", "radius": 8,
              "align": "left", "number": True, "divider": False}},
    {"id": "di-list", "name": "清单·米杏", "skeleton": "list",
     "skin": {"bg": "#efe6da", "primary": "#2f7a55", "font": "sans", "radius": 8,
              "align": "left", "number": False, "divider": True}},
    {"id": "di-card", "name": "卡片·浅咖", "skeleton": "card",
     "skin": {"bg": "#e7dccb", "primary": "#a8743a", "font": "round", "radius": 16,
              "align": "left", "number": False, "divider": False}},
    {"id": "di-compare", "name": "对比·暖白", "skeleton": "compare",
     "skin": {"bg": "#f7f2ec", "primary": "#2c3e50", "font": "sans", "radius": 8,
              "align": "left", "number": False, "divider": False}},
    {"id": "di-split", "name": "上图下文·米杏", "skeleton": "split",
     "skin": {"bg": "#efe6da", "primary": "#5a4636", "font": "serif", "radius": 8,
              "align": "left", "number": False, "divider": False}},
    {"id": "di-quote", "name": "金句·深棕", "skeleton": "quote",
     "skin": {"bg": "#3a2e26", "primary": "#f0e6d6", "font": "serif", "radius": 8,
              "align": "center", "number": False, "divider": False}},
]


# ---------- 校验工具 ----------
def _clean_color(value, fallback: str) -> str:
    s = str(value or "").strip()
    return s if _HEX_RE.match(s) else fallback


def _clamp_int(value, lo: int, hi: int, fallback: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, n))


def _clean_skin(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "bg": _clean_color(raw.get("bg"), "#f7f2ec"),
        "primary": _clean_color(raw.get("primary"), "#e22a43"),
        "font": raw.get("font") if raw.get("font") in FONT_IDS else "sans",
        "radius": _clamp_int(raw.get("radius"), 0, 28, 8),
        "align": raw.get("align") if raw.get("align") in ALIGN_IDS else "left",
        "number": bool(raw.get("number")),
        "divider": bool(raw.get("divider")),
    }


def _clean_text(item: dict) -> dict:
    return {
        "id": str(item.get("id") or "") or uuid.uuid4().hex[:10],
        "name": str(item.get("name", "") or "").strip()[:_MAX_NAME],
        "desc": str(item.get("desc", "") or "").strip()[:_MAX_SHORT],
        "prompt": str(item.get("prompt", "") or "").strip(),
        "scene": str(item.get("scene", "") or "").strip()[:_MAX_NAME],
    }


def _clean_image(item: dict) -> dict:
    skeleton = item.get("skeleton")
    return {
        "id": str(item.get("id") or "") or uuid.uuid4().hex[:10],
        "name": str(item.get("name", "") or "").strip()[:_MAX_NAME],
        "skeleton": skeleton if skeleton in SKELETON_IDS else "num",
        "skin": _clean_skin(item.get("skin")),
    }


# ---------- 读取 ----------
def _load_raw() -> dict:
    d = read_json_safe(_path())
    if not isinstance(d, dict):
        d = {}
    return d


def load() -> dict:
    """返回校验后的两组模板；任一组缺失或为空都回落默认。"""
    d = _load_raw()
    text = d.get("text")
    image = d.get("image")
    text_out = [_clean_text(x) for x in text] if isinstance(text, list) and text else [dict(x) for x in DEFAULT_TEXT]
    image_out = [_clean_image(x) for x in image] if isinstance(image, list) and image else [dict(x) for x in DEFAULT_IMAGE]
    return {"text": text_out, "image": image_out}


def list_text() -> list[dict]:
    return load()["text"]


def list_image() -> list[dict]:
    return load()["image"]


# ---------- 写入 ----------
def save_text(item: dict) -> dict:
    """按 id 新增或更新文字模板；name/prompt 必填，否则抛 ValueError。"""
    row = _clean_text(item)
    if not row["name"]:
        raise ValueError("文字模板名称必填")
    if not row["prompt"]:
        raise ValueError("请填写给 AI 的写作要求")
    d = load()
    for i, p in enumerate(d["text"]):
        if p.get("id") == row["id"]:
            d["text"][i] = row
            write_json(_path(), d)
            return row
    d["text"].insert(0, row)
    d["text"] = d["text"][:_MAX_ITEMS]
    write_json(_path(), d)
    return row


def save_image(item: dict) -> dict:
    """按 id 新增或更新图片模板；name 必填，否则抛 ValueError。"""
    row = _clean_image(item)
    if not row["name"]:
        raise ValueError("图片模板名称必填")
    d = load()
    for i, p in enumerate(d["image"]):
        if p.get("id") == row["id"]:
            d["image"][i] = row
            write_json(_path(), d)
            return row
    d["image"].insert(0, row)
    d["image"] = d["image"][:_MAX_ITEMS]
    write_json(_path(), d)
    return row


def delete_text(template_id: str) -> bool:
    d = load()
    n = len(d["text"])
    d["text"] = [t for t in d["text"] if t.get("id") != template_id]
    write_json(_path(), d)
    return len(d["text"]) != n


def delete_image(template_id: str) -> bool:
    d = load()
    n = len(d["image"])
    d["image"] = [t for t in d["image"] if t.get("id") != template_id]
    write_json(_path(), d)
    return len(d["image"]) != n


def reset() -> dict:
    d = {"text": [dict(x) for x in DEFAULT_TEXT], "image": [dict(x) for x in DEFAULT_IMAGE]}
    write_json(_path(), d)
    return d


def reset_group(kind: str) -> dict:
    """只恢复一组（文字视图/图片视图各自的「恢复默认」按钮）。"""
    d = load()
    if kind == "text":
        d["text"] = [dict(x) for x in DEFAULT_TEXT]
    elif kind == "image":
        d["image"] = [dict(x) for x in DEFAULT_IMAGE]
    else:
        raise ValueError("kind 只能是 text 或 image")
    write_json(_path(), d)
    return d
