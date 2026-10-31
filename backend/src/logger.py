"""统一日志 + 账号审计 + 敏感数据脱敏（架构改造 P1）。

职责：
- 标准 logging：控制台 + data/logs/app.log（RotatingFileHandler，5MB × 5）
- 按模块命名 logger：src.engine / src.server / src.risk ...
- 账号级审计：登录/导 Cookie/采集/克隆/发布/草稿 留痕（含 account_id + 动作 + 结果 + 耗时）
- 脱敏：Cookie 值 / web_session / api_key 一律遮蔽，日志绝不出现明文

用法：
    from src import logger as xlog
    xlog.audit(account_id="default", action="collect", ok=True, detail="...")
    xlog.get("engine").warning("连接重试 %s", n)
    xlog.redact("web_session=abc123")  # → "web_session=***"
"""
import datetime
import json
import logging
import logging.handlers
import pathlib
import re
import time

# ---------------------------------------------------------------------------
# 敏感值遮蔽
# ---------------------------------------------------------------------------
_SENSITIVE_KEYS = (
    "web_session", "last_web_session", "a1", "api_key", "apikey", "cookie",
    "cookies", "xsec_token", "sessionid", "session_id", "password", "token",
)
# 键后允许引号（JSON "key":"value" / "key"="value" 均命中）；值段非引号/空白/分隔符，长度 ≥1
_KEY_RE = re.compile(
    r"(?i)(?:%s)\s*[\"']?\s*[=:]\s*[\"']?([^\"'\s;,&]{1,})" % "|".join(map(re.escape, _SENSITIVE_KEYS))
)


def redact(text: str) -> str:
    """把文本中形如 web_session=xxx / "api_key":"yyy" 的敏感值替换为 ***。

    保底规则：凡命中敏感 key 的值一律遮蔽（宁可多遮不可漏），长度<6 的短值也遮。
    """
    if not text:
        return text
    return _KEY_RE.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=***", text)


def redact_cookies(cookies: list[dict]) -> list[dict]:
    """Cookie 列表 → 脱敏展示（只留 name/domain，值全部遮蔽）。"""
    out = []
    for c in cookies or []:
        d = {"name": c.get("name", ""), "domain": c.get("domain", "")}
        if c.get("value"):
            d["value"] = "***"
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# logging 初始化（幂等）
# ---------------------------------------------------------------------------
_LOGS_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "logs"
_INITIALIZED = False


def _setup() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger("src")
    root.setLevel(logging.INFO)
    # 控制台（app.log 双写，避免 print 时代排障信息丢失）
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    # 滚动文件
    fh = logging.handlers.RotatingFileHandler(
        _LOGS_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _INITIALIZED = True


def get(name: str) -> logging.Logger:
    """取模块 logger（如 get("engine") → src.engine）。"""
    _setup()
    return logging.getLogger(f"src.{name}")


# ---------------------------------------------------------------------------
# 账号审计
# ---------------------------------------------------------------------------
_audit_logger = None


def _audit() -> logging.Logger:
    global _audit_logger
    _setup()
    if _audit_logger is None:
        _audit_logger = logging.getLogger("src.audit")
    return _audit_logger


def audit(account_id: str = "default", action: str = "", ok: bool = True,
          detail: str = "", cost_ms: int | None = None) -> None:
    """账号级操作留痕。detail 必须过 redact()，禁止传 Cookie 明文。"""
    entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account": account_id or "default",
        "action": action,
        "ok": ok,
        "detail": redact(detail or "")[:500],
    }
    if cost_ms is not None:
        entry["cost_ms"] = int(cost_ms)
    _audit().info(json.dumps(entry, ensure_ascii=False))


class Timer:
    """耗时测量（审计用）：with xlog.Timer() as t: ... → t.ms。"""

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self._t0) * 1000)
        return False
