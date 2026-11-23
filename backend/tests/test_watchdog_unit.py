import os

import pytest  # noqa: F401（fixture 由 pytest 注入）

from src import watchdog

class TestWindowsSpawn:
    """Windows 没有 os.fork：改用 Popen(stdin=PIPE) + 环境变量，语义必须与 fork 版一致。"""

    def test_can_start_ok_with_marks(self):
        assert watchdog._can_start(["marker"], "") is True

    def test_can_start_needs_marks(self):
        assert watchdog._can_start([], "") is False

    def test_spawn_win_passes_marks_and_holds_stdin(self, monkeypatch, tmp_path):
        seen = {}

        class _P:
            pid = 4242
            stdin = type("S", (), {"close": lambda self: seen.setdefault("closed", True)})()

        def fake_popen(cmd, **kw):
            seen["cmd"] = cmd
            seen["stdin"] = kw.get("stdin")
            seen["env"] = kw.get("env")
            seen["flags"] = kw.get("creationflags")
            return _P()

        monkeypatch.setattr(watchdog, "subprocess", type("S", (), {"Popen": staticmethod(fake_popen),
                                                                 "PIPE": object(), "DEVNULL": object()})())
        monkeypatch.setattr(watchdog.sys, "frozen", False, raising=False)
        monkeypatch.setattr(watchdog.sys, "argv", ["launcher.py"])
        monkeypatch.setattr(watchdog, "_CHILD_PROC", None, raising=False)
        w, pid = watchdog._spawn_win(["m1"], str(tmp_path / "wd.log"))
        assert pid == 4242 and w == watchdog._WIN_FD_MARK
        assert seen["env"]["XHS_WD_CHILD"] == "1"
        assert seen["env"]["XHS_WD_MARKS"] == '["m1"]'
        assert seen["stdin"] is watchdog.subprocess.PIPE
        assert seen["flags"] == watchdog._CREATE_NO_WINDOW

    def test_child_main_stdin_reaps_on_eof(self, monkeypatch, tmp_path):
        reaped = []
        monkeypatch.setenv("XHS_WD_MARKS", '["m1"]')
        monkeypatch.setenv("XHS_WD_LOG", str(tmp_path / "wd.log"))
        monkeypatch.setattr(watchdog, "_reap", lambda marks, log: reaped.append(marks))

        class _Stdin:
            class buffer:
                @staticmethod
                def read():
                    return b""

        monkeypatch.setattr(watchdog.sys, "stdin", _Stdin())

        class _Exit(Exception):
            pass

        def fake_exit(code):
            raise _Exit

        monkeypatch.setattr(watchdog.os, "_exit", fake_exit)
        try:
            watchdog.child_main_stdin()
        except _Exit:
            pass
        assert reaped == [["m1"]]
        log = (tmp_path / "wd.log").read_text(encoding="utf-8")
        assert "armed" in log and "eof" in log and "exit" in log
