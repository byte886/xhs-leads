"""每账号风控状态机 + 采集节流（架构改造 P1）。

把散落在 server（采集 90s 全局节流、发布 24h 熔断）与 browser_client（登录墙检测）
的风控信号，收拢为【按账号隔离】的状态机：

    ok ──(登录墙/验证码/限流)──► cooldown（熔断 until=now+N，硬停该账号）
    ok ──(采集频率超闸)────────► throttle（仅该账号拒绝新采集 N 秒）
    任何 ──(Cookie 失效)──────► manual（需扫码/导 Cookie，界面引导）
    cooldown/manual ──(人工确认 resolve)──► ok

关键性质：
- 每账号独立：A 号风控不影响 B 号（采集号被封，发布号照常）
- 状态持久化到 data/risk_state.json（重启后端不丢熔断）
- 审计：风控事件全部走 logger.audit

用法：
    from src import risk
    risk.check_throttle("collect_a")       # 采集前置：超闸抛 BlockedError
    risk.mark_risk("collect_a", "登录墙")   # 采集/发布失败时标记熔断
    risk.resolve("collect_a")              # 人工确认后解锁
"""
import datetime
import json
import pathlib
import time

from src import logger as xlog

_STATE_FILE = pathlib.Path(__file__).resolve().parents[2] / "data" / "risk_state.json"

# 默认参数（config.json 可覆盖）
DEFAULT_THROTTLE_SECONDS = 90.0     # 采集节流：距上次任务不足该值拒绝
DEFAULT_COOLDOWN_SECONDS = 24 * 3600  # 风控熔断：24h 硬停（发布护栏同量级）
DEFAULT_MANUAL_REASON = "需人工确认账号状态"


class BlockedError(Exception):
    """账号被风控熔断 / 节流拦截（调用方捕获后转 HTTP 4xx 或中止任务）。"""

    def __init__(self, account_id: str, reason: str, until: float | None = None):
        self.account_id = account_id
        self.reason = reason
        self.until = until
        super().__init__(reason)


# ---------------------------------------------------------------------------
# 状态存储（内存 + 落盘）
# ---------------------------------------------------------------------------
_STATES: dict[str, dict] = {}  # account_id -> {status, until, reason, last_collect_end_at}
_LOADED = False


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf8"))
            _STATES.update(data.get("accounts", {}))
    except Exception:  # noqa: BLE001
        pass
    _LOADED = True


def _save() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"accounts": _STATES}, ensure_ascii=False, indent=2),
            encoding="utf8",
        )
    except Exception:  # noqa: BLE001
        pass


def _state(account_id: str) -> dict:
    _load()
    s = _STATES.setdefault(account_id or "default", {
        "status": "ok", "until": 0.0, "reason": "",
        "last_collect_end_at": 0.0,
    })
    return s


def _expired(s: dict) -> bool:
    """熔断/节流是否已到期（到期自动回到 ok）。"""
    return s.get("status") in ("cooldown", "throttle") and s.get("until", 0.0) <= time.time()


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def state(account_id: str = "default") -> dict:
    """账号当前风控状态（到期自动回落）。"""
    s = _state(account_id)
    if _expired(s):
        s["status"] = "ok"
        s["reason"] = ""
        _save()
    return dict(s)


def is_blocked(account_id: str = "default") -> tuple[bool, str]:
    """是否被风控拦截（cooldown 或 manual）。返回 (是否拦截, 原因)。"""
    s = _state(account_id)
    if _expired(s):
        s["status"] = "ok"
        s["reason"] = ""
        _save()
        return False, ""
    if s.get("status") == "cooldown":
        until = s.get("until", 0.0)
        wait = max(0, int(until - time.time()))
        return True, f"账号处于风控熔断中（{s.get('reason') or '平台风险拦截'}），请 {wait // 3600} 小时后再试或人工确认解锁"
    if s.get("status") == "manual":
        return True, (s.get("reason") or DEFAULT_MANUAL_REASON) + "（页面已打开登录入口，请扫码或完成验证，恢复后自动解除）"
    return False, ""


# ---------------------------------------------------------------------------
# 标记
# ---------------------------------------------------------------------------
def mark_soft(account_id: str = "default", reason: str = "临时风控（验证码/登录墙）") -> None:
    """软风控：验证码/登录墙等【临时异常】——不是被封禁，扫码或图形验证即可恢复。

    处置：状态 throttle + 指数退避冷却（1→2→4→8→16 分钟，封顶 30 分钟），
    冷却到期自动回 ok，不要求人工；同一任务由调用方在恢复后自然重试。
    绝不 24h 死等——用户随时配合扫码。
    """
    s = _state(account_id)
    n = int(s.get("soft_count", 0)) + 1
    s["soft_count"] = n
    secs = min(60 * (2 ** (min(n, 5) - 1)), 1800)  # 1→2→4→8→16 分钟，封顶 30 分钟
    s["status"] = "throttle"
    s["until"] = time.time() + secs
    s["reason"] = f"{reason}（临时风控第 {n} 次，冷却 {secs // 60} 分钟，扫码或完成验证后自动恢复）"
    _save()
    xlog.audit(account_id, action="risk_soft", ok=False,
               detail=f"软风控冷却 {secs // 60} 分钟（第 {n} 次）：{reason}")


def mark_manual(account_id: str = "default", reason: str = "Cookie 失效，需重新扫码或导入 Cookie") -> None:
    """标记需人工介入（登录失效/验证码/图形验证）：界面引导扫码/验证，业务动作拒绝。

    与 mark_soft 的区别：manual 需要【登录成功事件】才自动恢复（server 在 login_status
    检测到登录态有效时调用 resolve），适合明确出现登录墙/扫码页的场景。
    """
    s = _state(account_id)
    s["status"] = "manual"
    s["until"] = 0.0
    s["reason"] = reason
    _save()
    xlog.audit(account_id, action="risk_manual", ok=False, detail=reason)


def mark_hard(account_id: str = "default", reason: str = "平台明确风控/封禁", cooldown_seconds: float | None = None) -> None:
    """硬风控：账号被明确拦截/封禁，硬停（默认 24h）。绝不自动重试。"""
    s = _state(account_id)
    secs = cooldown_seconds if cooldown_seconds is not None else DEFAULT_COOLDOWN_SECONDS
    s["status"] = "cooldown"
    s["until"] = time.time() + secs
    s["reason"] = reason or "平台风险拦截"
    s["soft_count"] = 0
    _save()
    xlog.audit(account_id, action="risk_hard", ok=False,
               detail=f"硬风控熔断 {secs // 3600}h：{s['reason']}")


def mark_risk(account_id: str = "default", reason: str = "", cooldown_seconds: float | None = None) -> None:
    """（兼容旧名）硬风控熔断：该账号硬停（默认 24h）。"""
    mark_hard(account_id, reason, cooldown_seconds)


def mark_manual(account_id: str = "default", reason: str = "Cookie 失效，需重新扫码或导入 Cookie") -> None:
    """标记需人工介入（登录失效/验证码）：界面引导扫码/导入，业务动作拒绝。"""
    s = _state(account_id)
    s["status"] = "manual"
    s["until"] = 0.0
    s["reason"] = reason
    _save()
    xlog.audit(account_id, action="risk_manual", ok=False, detail=reason)


def mark_throttle(account_id: str = "default", reason: str = "操作过于频繁") -> None:
    """临时节流（秒级）：仅拒绝新采集任务，不熔断。"""
    s = _state(account_id)
    s["status"] = "throttle"
    s["until"] = time.time() + 20
    s["reason"] = reason
    _save()


def resolve(account_id: str = "default", reason: str = "登录恢复/人工确认") -> None:
    """解锁：登录成功或人工确认账号正常后恢复，同时清零连续软风控计数。"""
    s = _state(account_id)
    s["status"] = "ok"
    s["until"] = 0.0
    s["reason"] = ""
    s["soft_count"] = 0
    _save()
    xlog.audit(account_id, action="risk_resolve", ok=True, detail=reason)


# ---------------------------------------------------------------------------
# 采集节流（每账号，替代 server 全局 _check_collect_throttle）
# ---------------------------------------------------------------------------
def check_throttle(account_id: str = "default", min_interval: float | None = None) -> None:
    """采集/搜索前置节流闸：距该账号上次平台任务不足阈值 → 抛 BlockedError。

    min_interval 缺省 90s（config.collect_min_interval 可覆盖）。
    """
    s = _state(account_id)
    if _expired(s):
        s["status"] = "ok"
    if s.get("status") == "cooldown":
        wait = max(0, int(s.get("until", 0) - time.time()))
        raise BlockedError(account_id, f"账号风控熔断中，请 {wait // 60} 分钟后再试")
    if s.get("status") == "manual":
        raise BlockedError(account_id, s.get("reason") or DEFAULT_MANUAL_REASON)
    interval = min_interval if min_interval is not None else DEFAULT_THROTTLE_SECONDS
    elapsed = time.time() - s.get("last_collect_end_at", 0.0)
    if elapsed < interval:
        wait = int(interval - elapsed) + 1
        raise BlockedError(account_id, f"操作过于频繁，请 {wait} 秒后再试（拟人频率保护）")


def mark_collect_done(account_id: str = "default") -> None:
    """平台任务结束（成功/停止/失败）统一记录，供节流闸使用。"""
    s = _state(account_id)
    s["last_collect_end_at"] = time.time()
    _save()
