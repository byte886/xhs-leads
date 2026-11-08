"""扩展自动加载能力（R-6.70，方向 A）：目录解析 + 按账号开关 + 启动参数注入。

默认关闭是硬约束：开关不打开时，任何启动参数、账号行为都不变。
"""
import pytest

from src import server  # noqa: F401（先导入 server 打破循环）
from src import config, engine_pool, engine_types


class TestExtensionCandidates:
    def test_candidates_include_dev_root(self):
        cands = engine_types.builtin_extension_candidates()
        assert cands and isinstance(cands, list)
        assert any(c.endswith("extension") for c in cands)

    def test_dir_none_without_manifest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            engine_types, "builtin_extension_candidates",
            lambda: [str(tmp_path / "empty")])
        assert engine_types.builtin_extension_dir() is None

    def test_dir_found_with_manifest(self, tmp_path, monkeypatch):
        ext = tmp_path / "ext"
        ext.mkdir()
        (ext / "manifest.json").write_text("{}")
        monkeypatch.setattr(
            engine_types, "builtin_extension_candidates",
            lambda: [str(ext)])
        assert engine_types.builtin_extension_dir() == str(ext)


@pytest.fixture
def cfg_files(tmp_path, monkeypatch):
    """账号注册表与全局配置都指向临时文件，避免污染真实数据。"""
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config._write_json(config.ACCOUNTS_FILE, {"accounts": {}, "active": "default"})


class TestExtensionConfigSwitch:
    def test_default_disabled(self, cfg_files):
        config.add_account("acc_a")
        assert config.extension_enabled("acc_a") is False

    def test_legacy_account_without_field_is_false(self, cfg_files):
        # 模拟老账号：手工写入不含 extension_enabled 的记录，无需迁移即按 False 处理。
        config._write_json(
            config.ACCOUNTS_FILE, {"accounts": {"old": {"role": "mixed"}}})
        assert config.extension_enabled("old") is False

    def test_enable_and_disable(self, cfg_files):
        config.add_account("acc_a")
        assert config.set_extension_enabled("acc_a", True) is True
        assert config.extension_enabled("acc_a") is True
        config.set_extension_enabled("acc_a", False)
        assert config.extension_enabled("acc_a") is False

    def test_unknown_account_rejected(self, cfg_files):
        assert config.set_extension_enabled("ghost", True) is False
        assert "ghost" not in config.list_accounts()

    def test_one_account_does_not_affect_others(self, cfg_files):
        config.add_account("acc_a")
        config.add_account("acc_b")
        config.set_extension_enabled("acc_a", True)
        assert config.extension_enabled("acc_a") is True
        assert config.extension_enabled("acc_b") is False


class TestLaunchApplyExtension:
    def _engine(self):
        eng = engine_pool.AccountEngine.__new__(engine_pool.AccountEngine)
        eng.account_id = "acc_a"
        return eng

    def test_no_dir_appends_nothing(self, monkeypatch):
        monkeypatch.setattr(engine_pool, "builtin_extension_dir", lambda: None)
        kwargs = {"args": []}
        self._engine()._launch_apply_extension(kwargs)
        assert kwargs["args"] == []

    def test_dir_appends_two_flags(self, monkeypatch):
        monkeypatch.setattr(
            engine_pool, "builtin_extension_dir", lambda: "/x/extension")
        kwargs = {"args": []}
        self._engine()._launch_apply_extension(kwargs)
        assert kwargs["args"] == [
            "--load-extension=/x/extension",
            "--disable-extensions-except=/x/extension",
        ]
