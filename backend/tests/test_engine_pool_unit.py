"""engine_pool 纯函数特征测试（R-6.36b：屏幕探测/窗口几何现行行为固化）。"""
import asyncio
import json

from src import server  # noqa: F401（先导入 server 打破循环）
from src import engine_pool, config, injections


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
        # R-6.80：高度不再写死 800，改为 min(1200, 屏高-60)；2880x1800 → 1200 封顶
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (2880, 1800))
        w, h, left, right = engine_pool.shell_window_geometry()
        assert w == 1280 and h == 1200 and left == 0 and right == 1280

    def test_narrow_screen_half(self, monkeypatch):
        # 1400x900 → 宽平分 700，高 900-60=840
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (1400, 900))
        w, h, left, right = engine_pool.shell_window_geometry()
        assert w == 700 and right == 700 and h == 840

    def test_tiny_screen_clamped_to_min(self, monkeypatch):
        # 800x600 极小屏：宽 400→钳到 640，高 540 保持（高于下限 480）
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (800, 600))
        w, h, _, _ = engine_pool.shell_window_geometry()
        assert w == engine_pool.MIN_WIN_W == 640 and h == 540

    def test_height_floor_on_very_short_screen(self, monkeypatch):
        # 1920x500 极矮屏：500-60=440 < 480，抬到下限
        monkeypatch.setattr(engine_pool, "probe_screen_size", lambda: (1920, 500))
        _, h, _, _ = engine_pool.shell_window_geometry()
        assert h == engine_pool.MIN_WIN_H == 480


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

    def _build_engine(self, monkeypatch, account_id="acc_x", pages=None):
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = account_id
        calls = []

        class FakeCtx:
            def __init__(self):
                self.pages = pages or []

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
            pages = []

            async def add_init_script(self, js):
                if "reds-mask" in js or "xhs_leads_lock" in js:
                    raise RuntimeError("cdp down")

        eng._context = BoomCtx()
        # 注入失败只告警，不抛（引擎启动与 cookie 回注照常）
        asyncio.run(eng._launch_after_start())

    def test_account_window_title_uses_identity(self, monkeypatch):
        """非 default 账号：初始页也要挂标题守护，标题里带昵称与小红书号。"""
        from src import config

        monkeypatch.setattr(config, "ACCOUNTS_FILE", config.DATA_DIR / "_t.json")
        got = []

        class FakePage:
            async def add_init_script(self, js):
                got.append(js)

            async def evaluate(self, js):
                got.append(js)

        monkeypatch.setattr(config, "account_display_name", lambda _a: "小红薯")
        monkeypatch.setattr(config, "account_red_id", lambda _a: "123456789")
        eng = self._build_engine(monkeypatch, account_id="verify_c", pages=[FakePage()])
        asyncio.run(eng._launch_after_start())
        assert got, "初始页必须挂标题守护"
        assert "__LABEL__" not in got[0], "占位符必须被替换，否则标题栏显示 __LABEL__"
        assert "小红薯" in got[0] and "123456789" in got[0]

    def test_default_skips_account_title(self, monkeypatch):
        """default 承载 APP 壳窗，标题由 shell_title 恒定守护，不能被账号标题覆盖。"""
        got = []

        class FakePage:
            async def add_init_script(self, js):
                got.append(js)

            async def evaluate(self, js):
                got.append(js)

        eng = self._build_engine(monkeypatch, account_id="default", pages=[FakePage()])
        asyncio.run(eng._launch_after_start())
        assert got == []


class TestRefreshWindowTitle:
    """登录成功/手动识别后，已开的窗口标题要立刻显示昵称+小红书号。"""

    def test_applies_to_every_open_page(self, monkeypatch):
        from src import config

        monkeypatch.setattr(config, "account_display_name", lambda _a: "小红薯")
        monkeypatch.setattr(config, "account_red_id", lambda _a: "123456789")
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = "verify_c"
        hits = []

        class FakePage:
            async def add_init_script(self, js):
                hits.append("init")

            async def evaluate(self, js):
                hits.append("eval:" + js[:20])

        class FakeCtx:
            pages = [FakePage(), FakePage()]

        async def _ctx():
            return FakeCtx()

        monkeypatch.setattr(eng, "context", _ctx)
        asyncio.run(eng.refresh_window_title())
        # 两个页面各 evaluate 一次（防重标记只挡 init script）
        assert sum(1 for h in hits if h.startswith("eval:")) == 2
        assert sum(1 for h in hits if h == "init") == 2

    def test_failure_is_swallowed(self, monkeypatch):
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = "verify_c"

        async def _boom():
            raise RuntimeError("cdp down")

        monkeypatch.setattr(eng, "context", _boom)
        asyncio.run(eng.refresh_window_title())  # 不抛


class TestAccountGeolocation:
    """R-6.81：同城/附近依赖的固定坐标解析（账号配置 > 环境变量 > 默认运营地）。"""

    def _use_accounts(self, monkeypatch, tmp_path, accounts):
        path = tmp_path / "accounts.json"
        path.write_text(json.dumps({"accounts": accounts, "active": "default"}), encoding="utf-8")
        monkeypatch.setattr(config, "ACCOUNTS_FILE", path)
        monkeypatch.delenv("XHS_GEO_LAT", raising=False)
        monkeypatch.delenv("XHS_GEO_LNG", raising=False)
        return path

    def test_default_is_operating_city(self, monkeypatch, tmp_path):
        self._use_accounts(monkeypatch, tmp_path, {})
        assert config.account_geolocation("default") == config.DEFAULT_GEOLOCATION

    def test_account_override_wins(self, monkeypatch, tmp_path):
        self._use_accounts(monkeypatch, tmp_path, {
            "shenzhen": {"user_data_dir": "accounts/shenzhen",
                         "geolocation": {"latitude": 22.543, "longitude": 114.058}},
        })
        geo = config.account_geolocation("shenzhen")
        assert geo["latitude"] == 22.543 and geo["longitude"] == 114.058
        assert geo["accuracy"] == config.DEFAULT_GEOLOCATION["accuracy"]

    def test_env_used_when_account_has_none(self, monkeypatch, tmp_path):
        self._use_accounts(monkeypatch, tmp_path, {})
        monkeypatch.setenv("XHS_GEO_LAT", "30.0")
        monkeypatch.setenv("XHS_GEO_LNG", "120.0")
        geo = config.account_geolocation("default")
        assert geo["latitude"] == 30.0 and geo["longitude"] == 120.0

    def test_invalid_account_geo_falls_back(self, monkeypatch, tmp_path):
        self._use_accounts(monkeypatch, tmp_path, {
            "bad": {"user_data_dir": "accounts/bad",
                    "geolocation": {"latitude": "oops", "longitude": 112.1}},
        })
        assert config.account_geolocation("bad") == config.DEFAULT_GEOLOCATION


class TestLaunchApplyGeolocation:
    """启动参数注入：geolocation + permissions 必须落到 launch kwargs 上。"""

    def _engine(self):
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = "default"
        return eng

    def test_inject_kwargs(self, monkeypatch):
        eng = self._engine()
        monkeypatch.setattr(config, "account_geolocation",
                            lambda _a="default": {"latitude": 32.0, "longitude": 112.0, "accuracy": 50.0})
        kwargs = {}
        eng._launch_apply_geolocation(kwargs)
        assert kwargs["geolocation"] == {"latitude": 32.0, "longitude": 112.0, "accuracy": 50.0}
        assert kwargs["permissions"] == ["geolocation"]

    def test_headless_and_headed_both_get_it(self, monkeypatch):
        """定位与视口模式无关：无头、有头都必须带 geolocation。"""
        monkeypatch.setattr(engine_pool, "shell_window_geometry", lambda: (960, 1140, 0, 960))
        monkeypatch.setattr(config, "account_geolocation",
                            lambda _a="default": dict(config.DEFAULT_GEOLOCATION))
        for headless in (True, False):
            kwargs = self._engine()._launch_kwargs_base(headless, 9223)
            self._engine()._launch_apply_geolocation(kwargs)
            assert "geolocation" in kwargs
            assert kwargs["permissions"] == ["geolocation"]
