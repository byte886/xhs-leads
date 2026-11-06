"""进程级看门狗单测（R-6.53z8e）。

看门狗只做进程存活看门：管道 EOF 触发清理。这里守住三件事——
EOF 能唤醒、特征串非空、清理幂等可重复。
"""
import os

from src import watchdog


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
    assert all(isinstance(m, str) and m for m in marks)


def test_reap_calls_pkill_per_marker(monkeypatch):
    calls = []

    class _Done:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _Done()

    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    watchdog._reap(["marker-a", "marker-b", ""])
    assert len(calls) == 2  # 空串跳过
    assert calls[0][:3] == ["pkill", "-9", "-f"]
    assert calls[0][3] == "marker-a"


def test_reap_swallows_subprocess_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pkill")

    monkeypatch.setattr(watchdog.subprocess, "run", boom)
    watchdog._reap(["x"])  # 不抛即可：清理失败不影响退出
