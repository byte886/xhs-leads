"""进程级看门狗单测（R-6.53z8e / z8f）。

看门狗只做进程存活看门：管道 EOF 触发清理，且清理过程必须留痕——
主进程死后，watchdog.log 是唯一能回答「清了谁、成没成」的证据。
"""
import os

from src import watchdog


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_watch_until_eof_returns_when_writer_closed():
    r, w = os.pipe()
    os.close(w)
    watchdog._watch_until_eof(r)  # 写端关闭后必须立即返回，不得挂起
    os.close(r)


def test_watch_until_eof_ignores_bad_fd():
    watchdog._watch_until_eof(-1)  # 非法描述符：吞掉异常，不抛


def test_default_markers_not_empty():
    marks = watchdog.default_markers()
    assert marks, "无特征串时看门狗无法清理残留引擎"


def test_log_appends_timestamped_line(tmp_path):
    p = str(tmp_path / "wd.log")
    watchdog._log(p, "eof", "父进程已消失")
    body = open(p, encoding="utf-8").read()
    assert "[watchdog]" in body and "[eof]" in body and "父进程已消失" in body
    watchdog._log(p, "exit", "清理完成")
    assert len(open(p, encoding="utf-8").read().splitlines()) == 2  # 追加而非覆盖


def test_find_targets_matches_command_line(monkeypatch):
    out = (
        "  101 /x/engine/chrome-mac-x64/Contents/MacOS/Chrome --user-data-dir=/a\n"
        "  102 /usr/bin/unrelated\n"
        "  103 /x/.xhs-leads/accounts/default/SingletonLock\n"
    )

    class _R:
        stdout = out

    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _R())
    hits = watchdog._find_targets(["chrome-mac-x64", ".xhs-leads/accounts"])
    assert hits["chrome-mac-x64"] == [101]
    assert hits[".xhs-leads/accounts"] == [103]


def test_find_targets_survives_ps_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(watchdog.subprocess, "run", boom)
    assert watchdog._find_targets(["m"]) == {"m": []}  # ps 挂掉不抛，退化为空命中


def test_reap_kills_and_logs_each_marker(monkeypatch, tmp_path):
    p = str(tmp_path / "wd.log")
    killed = []
    monkeypatch.setattr(watchdog, "_find_targets", lambda m: {"m1": [11, 12], "m2": []})
    monkeypatch.setattr(watchdog.os, "kill", lambda pid, sig: killed.append(pid))
    watchdog._reap(["m1", "m2"], p)
    assert killed == [11, 12]
    body = open(p, encoding="utf-8").read()
    assert "命中 2 killed=2 pids=11,12" in body
    assert "无命中" in body  # 空命中也必须留痕，否则「没杀到」和「没跑」分不清


def test_reap_counts_already_dead_as_ok(monkeypatch, tmp_path):
    p = str(tmp_path / "wd.log")

    def gone(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(watchdog, "_find_targets", lambda m: {"m1": [9]})
    monkeypatch.setattr(watchdog.os, "kill", gone)
    watchdog._reap(["m1"], p)
    assert "killed=1" in open(p, encoding="utf-8").read()


def test_last_event_reads_tail(tmp_path):
    p = str(tmp_path / "wd.log")
    _write(
        p,
        "2026-11-06 10:00:00 [watchdog][pid=1][armed] x\n"
        "2026-11-06 10:05:00 [watchdog][pid=1][exit] 清理完成\n",
    )
    ev = watchdog._last_event(p)
    assert ev == ("2026-11-06 10:05:00", "exit")


def test_last_event_none_for_empty_or_missing(tmp_path):
    assert watchdog._last_event("") is None
    assert watchdog._last_event(str(tmp_path / "nope.log")) is None


def test_rotate_moves_oversized_log(tmp_path, monkeypatch):
    p = tmp_path / "wd.log"
    p.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(watchdog, "_LOG_KEEP_BYTES", 10)
    watchdog._rotate(str(p))
    assert not os.path.exists(str(p)) and os.path.exists(str(p) + ".1")
