"""risk.py 特征测试（R-6.36b：状态机现行行为固化，不改行为）。

R-6.15 拍板：check_throttle 恒放行；状态机本身（cooldown/manual/throttle/resolve）
仍落盘供审计。本文件固化这些现行语义。
"""
import time

import pytest

from src import risk


@pytest.fixture(autouse=True)
def _isolated_risk_file(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "_STATE_FILE", tmp_path / "risk.json")
    risk._STATES.clear()
    risk._LOADED = False
    yield
    risk._STATES.clear()


def test_state_default_ok():
    s = risk.state("acc_a")
    assert s["status"] == "ok" and s["until"] == 0.0


def test_mark_hard_blocks_then_expires_back_ok(monkeypatch):
    risk.mark_hard("acc_b", "封了", cooldown_seconds=10)
    blocked, reason = risk.is_blocked("acc_b")
    assert blocked is True and "熔断" in reason
    # 伪造到期 → state 自动回落 ok
    risk._STATES["acc_b"]["until"] = time.time() - 1
    s = risk.state("acc_b")
    assert s["status"] == "ok"


def test_mark_manual_blocks_until_resolve():
    risk.mark_manual("acc_c", "Cookie 失效")
    blocked, reason = risk.is_blocked("acc_c")
    assert blocked is True and "Cookie 失效" in reason
    risk.resolve("acc_c")
    blocked, _ = risk.is_blocked("acc_c")
    assert blocked is False


def test_mark_soft_backoff_progression():
    risk.mark_soft("acc_d", reason="验证码")
    s1 = risk.state("acc_d")
    risk.mark_soft("acc_d", reason="验证码")
    s2 = risk.state("acc_d")
    assert s1["status"] == "throttle"
    assert s1["soft_count"] == 1 and s2["soft_count"] == 2
    # 第二次冷却应更长（指数退避）
    assert s2["until"] - time.time() > s1["until"] - time.time() - 1


def test_mark_throttle_seconds():
    risk.mark_throttle("acc_e", "频繁")
    s = risk.state("acc_e")
    assert s["status"] == "throttle" and 0 < s["until"] - time.time() < 60


def test_check_throttle_always_pass_r615():
    """R-6.15 拍板：check_throttle 恒放行，不抛 BlockedError。"""
    risk.mark_hard("acc_f", "封", cooldown_seconds=9999)
    risk.check_throttle("acc_f")  # 不抛 = 通过


def test_is_blocked_ok_false():
    blocked, reason = risk.is_blocked("acc_g")
    assert blocked is False and reason == ""


def test_state_per_account_isolation():
    risk.mark_hard("acc_h1", "封", cooldown_seconds=9999)
    blocked1, _ = risk.is_blocked("acc_h1")
    blocked2, _ = risk.is_blocked("acc_h2")
    assert blocked1 is True and blocked2 is False


def test_mark_collect_done_records_ts():
    risk.mark_collect_done("acc_i")
    s = risk.state("acc_i")
    assert s["last_collect_end_at"] > 0


def test_persistence_roundtrip(monkeypatch, tmp_path):
    """落盘后重置内存再加载 → 状态回来。"""
    risk.mark_hard("acc_j", "封", cooldown_seconds=9999)
    risk._STATES.clear()
    risk._LOADED = False
    s = risk.state("acc_j")
    assert s["status"] == "cooldown"
