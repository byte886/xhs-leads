"""engine_pool 纯函数特征测试（R-6.36b：屏幕探测/窗口几何现行行为固化）。"""
import asyncio
import json

from src import server  # noqa: F401（先导入 server 打破循环）
from src import engine_pool, injections


class TestProbeScreenSize:
    def test_parse_resolution(self, monkeypatch):
        class R:
            stdout = " ...\n    Resolution: 2880 x 1800\n ..."
        monkeypatch.setattr(engine_pool.subprocess, "run", lambda *a, **k: R())
        engine_pool._SCREEN = None
        assert engine_pool.probe_screen_size() == (2880, 1800)

    def test_fallback_on_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no system_profiler")
        monkeypatch.setattr(engine_pool.subprocess, "run", boom)
        engine_pool._SCREEN = None
        assert engine_pool.probe_screen_size() == (1920, 1200)


class TestShellWindowGeometry:
    def test_left_right_split(self, monkeypatch):
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (2880, 1800))
        w, h, left, right = engine_pool.shell_window_geometry()
        assert w == 1280 and h == 800 and left == 0 and right == 1280

    def test_narrow_screen_half(self, monkeypatch):
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (1400, 900))
        w, h, left, right = engine_pool.shell_window_geometry()
        assert w == 700 and right == 700


class TestClearSessionRestore:
    """R-6.48s：启动前清会话恢复文件——异常退出恢复旧 tab（小红书页闪现）根因修复。"""

    def test_removes_sessions_and_current_files(self, tmp_path):
        default = tmp_path / "Default"
        sessions = default / "Sessions"
        sessions.mkdir(parents=True)
        (sessions / "Session_1").write_text("x")
        (sessions / "Tabs_1").write_text("x")
        (default / "Current Session").write_text("x")
        (default / "Current Tabs").write_text("x")
        engine_pool._clear_session_restore(str(tmp_path))
        assert list(sessions.iterdir()) == []
        assert not (default / "Current Session").exists()
        assert not (default / "Current Tabs").exists()

    def test_preserves_cookies_and_other_files(self, tmp_path):
        default = tmp_path / "Default"
        default.mkdir(parents=True)
        cookies = default / "Cookies"
        cookies.write_text("keep")
        (default / "Preferences").write_text("keep")
        engine_pool._clear_session_restore(str(tmp_path))
        assert cookies.read_text() == "keep"
        assert (default / "Preferences").read_text() == "keep"

    def test_missing_dirs_no_error(self, tmp_path):
        engine_pool._clear_session_restore(str(tmp_path / "nope"))  # 不抛错

    def test_fixes_crashed_exit_type_in_preferences(self, tmp_path):
        """R-6.49b：Preferences.exit_type=Crashed → Normal（根除「要恢复页面吗？」横幅）。"""
        default = tmp_path / "Default"
        default.mkdir(parents=True)
        (default / "Preferences").write_text(
            json.dumps({"exit_type": "Crashed", "profile": {"exit_type": "Crashed"}})
        )
        engine_pool._clear_session_restore(str(tmp_path))
        prefs = json.loads((default / "Preferences").read_text())
        assert prefs["exit_type"] == "Normal"
        assert prefs["profile"]["exit_type"] == "Normal"

    def test_normal_exit_type_untouched(self, tmp_path):
        """R-6.49b：已是 Normal 的 Preferences 保持原样（不无谓写盘）。"""
        default = tmp_path / "Default"
        default.mkdir(parents=True)
        (default / "Preferences").write_text(
            json.dumps({"exit_type": "Normal", "profile": {"exit_type": "Normal"}})
        )
        engine_pool._clear_session_restore(str(tmp_path))
        assert (default / "Preferences").read_text() == json.dumps(
            {"exit_type": "Normal", "profile": {"exit_type": "Normal"}}
        )


class TestLaunchAfterStartInjects:
    """启动后注入：stealth + force_login（登录框遮罩锁，防点空白误关）。"""

    def _build_engine(self, monkeypatch):
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = "acc_x"
        calls = []

        class FakeCtx:
            async def add_init_script(self, js):
                calls.append(js)

        eng._context = FakeCtx()
        eng._injected = calls

        async def _noop():
            return None

        monkeypatch.setattr(eng, "_replay_saved_cookies", _noop)
        return eng

    def test_injects_stealth_and_force_login(self, monkeypatch):
        eng = self._build_engine(monkeypatch)
        asyncio.run(eng._launch_after_start())
        assert injections.get("stealth") in eng._injected
        assert injections.get("force_login") in eng._injected

    def test_force_login_failure_does_not_break_start(self, monkeypatch):
        eng = self._build_engine(monkeypatch)

        class BoomCtx:
            async def add_init_script(self, js):
                if "reds-mask" in js or "xhs_leads_lock" in js:
                    raise RuntimeError("cdp down")

        eng._context = BoomCtx()
        # 注入失败只告警，不抛（引擎启动与 cookie 回注照常）
        asyncio.run(eng._launch_after_start())
