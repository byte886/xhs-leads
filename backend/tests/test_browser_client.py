"""浏览器客户端纯逻辑单测：滚动采集终止判定 + URL 编码 + CDP 路径可移植性。"""
import os

from src import browser_client as bc
from src import config


class TestShouldStopCollect:
    def test_count_reached(self):
        assert bc._should_stop_collect(30, 30, 0, 5, None) == (True, 0)

    def test_user_stop(self):
        assert bc._should_stop_collect(5, 30, 0, 1, lambda: True) == (True, 0)

    def test_stale_increments(self):
        stop, stale = bc._should_stop_collect(0, 30, 1, 0, None)
        assert stop is False and stale == 2

    def test_stale_stops_with_results(self):
        assert bc._should_stop_collect(10, 30, 1, 0, None)[0] is True

    def test_stale_resets_on_added(self):
        stop, stale = bc._should_stop_collect(10, 30, 2, 3, None)
        assert stop is False and stale == 0

    def test_no_results_no_stop(self):
        assert bc._should_stop_collect(0, 30, 1, 0, None)[0] is False


class TestUrlEncode:
    def test_quote_space(self):
        assert bc._urlencode("a b") == "a%20b"


class TestHomeRelative:
    """save_cdp_config 路径可移植：$HOME 下绝对路径 → ~ 相对形式。"""

    def test_home_prefix_to_tilde(self):
        home = os.path.expanduser("~")
        assert config.home_relative(home + "/.xhs-leads-profile") == "~/.xhs-leads-profile"

    def test_home_itself_to_tilde(self):
        assert config.home_relative(os.path.expanduser("~")) == "~"

    def test_non_home_path_unchanged(self):
        assert config.home_relative("/Applications/Google Chrome.app") == "/Applications/Google Chrome.app"
        assert config.home_relative("/x") == "/x"

    def test_empty_unchanged(self):
        assert config.home_relative("") == ""

    def test_profile_settings_expands_tilde(self, tmp_path, monkeypatch):
        from src import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "cdp_config.json").write_text('{"profile_dir": "~/.xhs-leads-profile"}', encoding="utf-8")
        monkeypatch.delenv("XHS_PROFILE_DIR", raising=False)
        _, profile_dir = bc._profile_settings()
        assert profile_dir == os.path.expanduser("~/.xhs-leads-profile")
        assert not profile_dir.startswith("~")

    def test_save_cdp_config_normalizes(self, tmp_path, monkeypatch):
        from src import config
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
        home = os.path.expanduser("~")
        bc.save_cdp_config(chrome_path="/Applications/Google Chrome.app",
                           profile_dir=home + "/.xhs-leads-profile")
        saved = config.get_config()
        assert saved["profile_dir"] == "~/.xhs-leads-profile"
        assert saved["chrome_path"] == "/Applications/Google Chrome.app"
