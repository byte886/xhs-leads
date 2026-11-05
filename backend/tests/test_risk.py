"""risk.py 特征测试（R-6.36b 固化；R-6.53z3 收敛为两态后重写）。

R-6.15 拍板：采集前置不再有频率闸/熔断闸。R-6.53z3 依「没有被调用的代码就是
死代码」删掉 cooldown/throttle 分支与 mark_soft/mark_hard/mark_risk/
mark_throttle/check_throttle/BlockedError，状态机只剩 ok 与 manual。本文件
固化这两态与落盘行为。
"""
from src import risk


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "_STATE_FILE", tmp_path / "risk.json")
    risk._STATES.clear()
    risk._LOADED = False


def test_state_default_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    s = risk.state("acc_a")
    assert s["status"] == "ok" and s["reason"] == ""


def test_mark_manual_blocks_until_resolve(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    risk.mark_manual("acc_c", "Cookie 失效")
    blocked, reason = risk.is_blocked("acc_c")
    assert blocked is True and "Cookie 失效" in reason
    risk.resolve("acc_c")
    blocked, _ = risk.is_blocked("acc_c")
    assert blocked is False


def test_state_per_account_isolation(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    risk.mark_manual("acc_h1", "需扫码")
    blocked1, _ = risk.is_blocked("acc_h1")
    blocked2, _ = risk.is_blocked("acc_h2")
    assert blocked1 is True and blocked2 is False


def test_is_blocked_ok_false(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    blocked, reason = risk.is_blocked("acc_g")
    assert blocked is False and reason == ""


def test_mark_collect_done_records_ts(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    risk.mark_collect_done("acc_i")
    s = risk.state("acc_i")
    assert s["last_collect_end_at"] > 0


def test_persistence_roundtrip(tmp_path, monkeypatch):
    """落盘后重置内存再加载 → 状态回来。"""
    _isolate(tmp_path, monkeypatch)
    risk.mark_manual("acc_j", "需扫码")
    risk._STATES.clear()
    risk._LOADED = False
    s = risk.state("acc_j")
    assert s["status"] == "manual"


def test_no_throttle_precheck_exists():
    """R-6.15/R-6.53z3：采集前置闸与硬熔断 API 已被删除，不再复活。"""
    for gone in ("check_throttle", "mark_hard", "mark_risk", "mark_soft",
                 "mark_throttle", "BlockedError"):
        assert not hasattr(risk, gone), gone
