"""engine_pool 纯函数特征测试（R-6.36b：屏幕探测/窗口几何现行行为固化）。"""
from src import server  # noqa: F401（先导入 server 打破循环）
from src import engine_pool


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
