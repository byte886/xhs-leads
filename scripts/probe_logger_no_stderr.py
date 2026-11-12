#!/usr/bin/env python3
"""验证 logger 在 sys.stderr=None（windowed 打包场景）下不再刷 Logging error。

用法：python probe_logger_no_stderr.py
"""
import io
import pathlib
import sys

REPO = pathlib.Path(r"\\Mac\Home\Desktop\codex-zhuke")
sys.path.insert(0, str(REPO / "backend"))

# 复刻 PyInstaller console=False：stderr 为 None
sys.stderr = None
buf = io.StringIO()
old_stdout, sys.stdout = sys.stdout, buf

from src import logger  # noqa: E402

lg = logger.get("probe")
lg.warning("搜索未进入结果页（停在 %s），判定失败、不采集", "https://x/explore")
lg.info("probe done")

sys.stdout = old_stdout
src_logger = __import__("logging").getLogger("src")
report = {
    "src_handlers": [type(h).__name__ for h in src_logger.handlers],
    "has_stream_handler": any(type(h).__name__ == "StreamHandler" for h in src_logger.handlers),
    "captured_stderr_output": buf.getvalue(),
}
print(report)
