"""每账号风控状态（架构改造 P1；R-6.53z3 清理为两态）。

把散落在 server（发布熔断）与 browser_client（登录墙检测）的风控信号，
收拢为【按账号隔离】的**两态**记录：

    ok ──(Cookie 失效 / 登录墙 / 验证码)──► manual（需扫码或导 Cookie）
    manual ──(登录态恢复 / 人工确认)──► ok

R-6.15 用户拍板：移除「拟人频率保护」任务间节流（90s）与「风控熔断/人工锁定」前置拦截。
R-6.53z3 依「没有被调用的代码就是死代码」进一步删除已无调用方的 cooldown/throttle
分支与 mark_soft / mark_hard / mark_risk / mark_throttle / check_throttle /
BlockedError —— 状态机只剩 ok 与 manual，采集前置不再有任何闸（与 R-6.15 一致）。

关键性质：
- 每账号独立：A 号需登录不影响 B 号
- 状态持久化到 data/risk_state.json（重启后端不丢）
- 审计：风控事件全部走 logger.audit
"""
import json
import pathlib
import time

from src import logger as xlog

_STATE_FILE = pathlib.Path(__file__).resolve().parents[2] / "data" / "risk_state.json"

DEFAULT_MANUAL_REASON = "需人工确认账号状态"


# ---------------------------------------------------------------------------
# 状态存储（内存 + 落盘）
# ---------------------------------------------------------------------------
_STATES: dict[str, dict] = {}  # account_id -> {status, reason, last_collect_end_at}
_LOADED = False


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf8"))
            _STATES.update(data.get("accounts", {}))
    except Exception:  # noqa: BLE001（状态文件损坏：按空状态起，不影响采集）
        pass
    _LOADED = True


def _save() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"accounts": _STATES}, ensure_ascii=False, indent=2),
            encoding="utf8",
        )
    except Exception:  # noqa: BLE001（落盘失败：内存态仍生效，仅丢审计）
        pass


def _state(account_id: str) -> dict:
    _load()
    return _STATES.setdefault(account_id or "default", {
        "status": "ok", "reason": "", "last_collect_end_at": 0.0,
    })


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def state(account_id: str = "default") -> dict:
    """账号当前风控状态（ok / manual）。"""
    return dict(_state(account_id))


def is_blocked(account_id: str = "default") -> tuple[bool, str]:
    """是否需要人工介入（manual）。返回 (是否拦截, 原因)。"""
    s = _state(account_id)
    if s.get("status") == "manual":
        return True, (s.get("reason") or DEFAULT_MANUAL_REASON) + "（页面已打开登录入口，请扫码或完成验证，恢复后自动解除）"
    return False, ""


# ---------------------------------------------------------------------------
# 标记
# ---------------------------------------------------------------------------
def mark_manual(account_id: str = "default", reason: str = "Cookie 失效，需重新扫码或导入 Cookie") -> None:
    """标记需人工介入（登录失效/验证码/图形验证）：界面引导扫码，业务动作拒绝。

    只有【登录成功事件】才自动恢复（server 在 login_status 检测到登录态有效时
    调 resolve），对应明确出现登录墙/扫码页的场景。
    """
    s = _state(account_id)
    s["status"] = "manual"
    s["reason"] = reason
    _save()
    xlog.audit(account_id, action="risk_manual", ok=False, detail=reason)


def resolve(account_id: str = "default", reason: str = "登录恢复/人工确认") -> None:
    """解锁：登录成功或人工确认账号正常后恢复。"""
    s = _state(account_id)
    s["status"] = "ok"
    s["reason"] = ""
    _save()
    xlog.audit(account_id, action="risk_resolve", ok=True, detail=reason)


def mark_collect_done(account_id: str = "default") -> None:
    """平台任务结束（成功/停止/失败）统一记录，供审计与后续诊断。"""
    s = _state(account_id)
    s["last_collect_end_at"] = time.time()
    _save()
