"""双窗口位置记忆（R-6.85）。

用户口径（2026-11-09 拍板）：
  - 下次启动**沿用最后一次摆放位置**；首次（无记录）用默认左右分屏
    （壳窗 left=0、采集窗 left=屏宽一半，两窗等宽、高=屏高-60）。
  - 记录发生在窗口被拖动/改大小之后，落盘到 `<data-dir>/window_layout.json`。

为什么不加后台常驻监听：
  MV3 扩展的 `chrome.windows.onBoundsChanged` 需要新增 `windows` 权限，而扩展当前只
  声明 tabs/tabGroups；为一个位置记录扩权限并新增跨进程通信不划算。这里用低频回读
  （默认 2s，见 shell_window.watch_window_layout）+ 变化才写盘，纯读取，
  不重建/不导航/不关闭任何窗口。
"""
from __future__ import annotations

import json
import pathlib

from src import config

FILE_NAME = "window_layout.json"
ROLES = ("shell", "collect")
MIN_W, MIN_H = 640, 480  # 与 engine_pool.MIN_WIN_W/MIN_WIN_H 同口径（避免循环导入故本地定义）


def path() -> pathlib.Path:
    return config.DATA_DIR / FILE_NAME


def load() -> dict[str, dict]:
    """读历史布局；文件缺失/损坏一律返回空 dict（调用方回退默认布局）。"""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for role in ROLES:
        b = raw.get(role) if isinstance(raw, dict) else None
        if isinstance(b, dict):
            out[role] = b
    return out


def clamp(bounds: dict, sw: int, sh: int) -> dict | None:
    """把记录钳到可用范围：尺寸不小于 MIN_*、不超屏；坐标保证窗口主体在屏内。

    负坐标（历史上把窗口挪到屏外的值）视为失效，由调用方回退默认布局。
    """
    try:
        left = int(bounds["left"])
        top = int(bounds["top"])
        width = int(bounds["width"])
        height = int(bounds["height"])
    except (KeyError, TypeError, ValueError):
        return None
    width = max(MIN_W, min(sw, width))
    height = max(MIN_H, min(sh, height))
    if left < 0 or top < 0:
        return None
    left = min(left, max(0, sw - 320))
    top = min(top, max(0, sh - 200))
    return {"left": left, "top": top, "width": width, "height": height}


def bounds_for(role: str, default: dict, sw: int, sh: int) -> dict:
    """取该角色的生效位置：有合法记录用记录，否则用默认布局。"""
    saved = load().get(role)
    if saved:
        c = clamp(saved, sw, sh)
        if c:
            return c
    return clamp(default, sw, sh) or dict(default)


def save(role: str, bounds: dict) -> bool:
    """写入某角色的最新位置；内容无变化返回 False（避免无意义落盘）。"""
    if role not in ROLES:
        return False
    cur = load()
    clean = {
        "left": int(bounds.get("left", 0)),
        "top": int(bounds.get("top", 0)),
        "width": int(bounds.get("width", 0)),
        "height": int(bounds.get("height", 0)),
    }
    if cur.get(role) == clean:
        return False
    cur[role] = clean
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path().with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path())  # 原子替换：写一半被 kill 也不会留下损坏文件
    except OSError:
        return False
    return True
