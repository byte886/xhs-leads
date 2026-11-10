"""行业 / 口吻 选项库（下拉候选，可维护、可恢复默认）。

为什么做成「选项」而不是自由文本：
  这两个字段会被拼进提示词（profiles_store.profile_prompt_block），
  自由填写时同一家店很容易写成「翡翠手镯」「翡翠」「翡翠手饰」三种说法，
  模型每次理解不同，生成风格就会漂。给一组稳定的默认值，用户能选、也能加。

但行业本身在变（新品类、跨行业复用），所以：内置默认 + 用户可增删改 + 一键恢复。
内置默认按珠宝行业起步，依据见本目录 OPTIONS-SOURCES.md。
存储：<运行数据目录>/ai_options.json
"""
from __future__ import annotations

from src import config
from .jsonio import read_json_safe, write_json

def _path():
    """运行时解析，测试里 monkeypatch config.DATA_DIR 才能生效。"""
    return config.DATA_DIR / "ai_options.json"

# 珠宝行业（按小红书/电商平台的类目粒度拆，不是按鉴定学的矿物学拆）
DEFAULT_INDUSTRIES = [
    "翡翠",
    "和田玉",
    "黄金",
    "钻石 / K金",
    "彩色宝石",
    "珍珠",
    "银饰",
    "琥珀蜜蜡",
    "绿松石 / 南红",
    "文玩手串",
    "设计师饰品",
    "其他珠宝",
]

# 口吻 = 说话的人是谁、站什么位置（不是「文体」）
DEFAULT_TONES = [
    "店主第一人称",
    "闺蜜安利",
    "专业测评",
    "避坑科普",
    "源头货主",
    "收藏玩家",
]

_MAX_ITEMS = 80
_MAX_LEN = 24


def defaults() -> dict:
    return {"industries": list(DEFAULT_INDUSTRIES), "tones": list(DEFAULT_TONES)}


def _clean_list(raw, fallback: list[str]) -> list[str]:
    """去空白、去重（保留首次出现顺序）、限长限量；整组为空时回落到默认。"""
    if not isinstance(raw, list):
        return fallback
    out: list[str] = []
    for v in raw:
        s = str(v or "").strip()[:_MAX_LEN]
        if not s or s in out:
            continue
        out.append(s)
        if len(out) >= _MAX_ITEMS:
            break
    return out or fallback


def load() -> dict:
    d = read_json_safe(_path())
    base = defaults()
    return {
        "industries": _clean_list(d.get("industries"), base["industries"]),
        "tones": _clean_list(d.get("tones"), base["tones"]),
    }


def save(payload: dict) -> dict:
    """整体保存（只覆盖传进来的那组），返回保存后的全量。"""
    cur = load()
    base = defaults()
    for key in ("industries", "tones"):
        if key in payload:
            cur[key] = _clean_list(payload.get(key), base[key])
    write_json(_path(), cur)
    return cur


def reset() -> dict:
    d = defaults()
    write_json(_path(), d)
    return d
