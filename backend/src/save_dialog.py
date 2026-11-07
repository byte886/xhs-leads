"""导出文件的保存位置与落盘。

背景（2026-11-08 取证）：APP 外壳是 CFT 窗口，浏览器 `showSaveFilePicker` 在里面
「立即成功」却不弹面板，文件被静默写进默认下载目录；改由后端用 osascript 弹
macOS 原生保存面板，实测面板在本进程上下文里**根本不显示**（截图验证，进程阻塞
但屏幕上无面板）。因此不再依赖系统对话框，改为应用内自绘「保存位置」对话框：
后端只提供可选目录（常用位置 + 目录浏览）与写盘能力，用户的选择完全可见可控。
"""
import sys
from pathlib import Path

_last_dir: str | None = None


def _remember(dir_path: Path) -> None:
    global _last_dir
    _last_dir = str(dir_path)


def places() -> dict:
    """常用保存位置（只返回真实存在的目录）+ 上次用过的目录。"""
    home = Path.home()
    named = {
        "downloads": home / "Downloads",
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "home": home,
    }
    out = {k: str(v) for k, v in named.items() if v.is_dir()}
    if _last_dir and Path(_last_dir).is_dir():
        out["last"] = _last_dir
    return out


def list_dirs(path: str) -> dict:
    """列出某目录下的子目录（隐藏目录跳过），供应用内目录浏览使用。"""
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise ValueError("路径不存在或不是文件夹")
    names = sorted(
        (d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith(".")),
        key=str.lower,
    )[:200]
    parent = str(target.parent) if target.parent != target else None
    return {
        "path": str(target),
        "parent": parent,
        "dirs": [{"name": n, "path": str(target / n)} for n in names],
    }


def write_bytes(path: str, data: bytes) -> Path:
    """把字节写到用户选定的路径；目标是文件夹或写入失败时抛 ValueError。"""
    target = Path(path).expanduser()
    if target.is_dir():
        raise ValueError("目标路径是一个文件夹，请选择具体文件名")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        raise ValueError(f"写入失败：{e}") from e
    _remember(target.parent)
    return target
