"""账号业务名（R-6.69）：account_id 是目录名，业务名是给人看的名字，两者分层。

覆盖：
- 业务名校验矩阵（中文可 / 换行禁 / 超长禁 / 空禁）
- 新增账号可带业务名；不带则 account_name 回退 id（不编造业务含义）
- 改名只动展示字段：不搬 profile 目录、不动登录态
- default 未在注册表时改名会补建条目
- 平台昵称读写（登录事件顺带缓存）
"""
import pytest

from src import config


@pytest.fixture
def reg(monkeypatch, tmp_path):
    """隔离注册表到 tmp，避免写真实 accounts.json。"""
    monkeypatch.setattr(config, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path / "accounts.json"


class TestValidAccountName:
    @pytest.mark.parametrize("name,expected", [
        ("珠宝主号", True),             # 中文业务名
        ("二号店·翡翠", True),          # 中文标点
        ("主号 main", True),            # 空格
        ("a", True),                    # 单字符
        ("名" * 24, True),              # 刚好 24
        ("名" * 25, False),             # 超长
        ("", False),                    # 空
        ("   ", False),                 # 全空白
        ("a\nb", False),                # 换行（控制字符）
        ("a\tb", False),                # 制表
        ("a/b", False),                 # 路径分隔符
        ("a\\b", False),                # Windows 路径分隔符
    ])
    def test_matrix(self, name, expected):
        assert config.valid_account_name(name) is expected


class TestAccountName:
    def test_falls_back_to_id_when_unset(self, reg):
        """没设业务名时显示 id——不替用户编业务含义。"""
        config.add_account("acc_a")
        assert config.account_name("acc_a") == "acc_a"

    def test_add_account_with_name(self, reg):
        config.add_account("zhubao_main", name="珠宝主号")
        assert config.account_name("zhubao_main") == "珠宝主号"

    def test_add_account_rejects_bad_name(self, reg):
        with pytest.raises(ValueError, match="账号名最多"):
            config.add_account("acc_x", name="名" * 25)

    def test_add_account_rejects_newline_name(self, reg):
        with pytest.raises(ValueError, match="控制字符"):
            config.add_account("acc_x", name="a\nb")

    def test_name_does_not_change_dir(self, reg):
        """改名关键约束：profile 目录与登录态原地不动。"""
        config.add_account("acc_a")
        before = config.get_account("acc_a")["user_data_dir"]
        assert config.set_account_name("acc_a", "珠宝主号") is True
        after = config.get_account("acc_a")
        assert after["user_data_dir"] == before
        assert after["name"] == "珠宝主号"
        assert not (config.DATA_DIR / "accounts" / "珠宝主号").exists()

    def test_rename_to_empty_clears_custom_name(self, reg):
        config.add_account("acc_a", name="临时名")
        assert config.set_account_name("acc_a", "") is True
        assert config.account_name("acc_a") == "acc_a"

    def test_rename_bad_name_rejected(self, reg):
        config.add_account("acc_a")
        assert config.set_account_name("acc_a", "名" * 25) is False
        assert config.account_name("acc_a") == "acc_a"

    def test_rename_unknown_account(self, reg):
        assert config.set_account_name("nope", "名字") is False

    def test_rename_default_not_in_registry_backfills(self, reg):
        """default 历史账号可能不在注册表：改名要先补建条目，不能静默失败。"""
        assert config.list_accounts() == {}
        assert config.set_account_name("default", "主账号") is True
        assert config.account_name("default") == "主账号"
        assert "default" in config.list_accounts()


class TestNickname:
    def test_read_write(self, reg):
        config.add_account("acc_a")
        assert config.account_nickname("acc_a") == ""
        assert config.set_account_nickname("acc_a", "普通人") is True
        assert config.account_nickname("acc_a") == "普通人"

    def test_blank_nickname(self, reg):
        config.add_account("acc_a", name="珠宝主号")
        config.set_account_nickname("acc_a", "  ")
        assert config.account_nickname("acc_a") == ""

    def test_unknown_account(self, reg):
        assert config.set_account_nickname("nope", "x") is False
        assert config.account_nickname("nope") == ""
