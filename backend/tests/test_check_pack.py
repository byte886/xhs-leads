"""打包产物自检的"用户数据 / 凭证"硬门单测。

背景：2026-11 真实事故——配置路径基于 __file__ 定位，打包态把 AI Key 写进
安装目录 data/，一旦把安装目录打成包就等于把用户密钥分发给所有人。
密钥只能由用户在「AI 设置」里自己粘贴，产物里一个都不许有。
"""
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "packaging"))

import check_pack  # noqa: E402
import spec_common  # noqa: E402


def test_finds_shipped_user_data_dir(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "llm_config.json").write_text("{}", encoding="utf-8")
    problems = check_pack.find_shipped_user_data(tmp_path, tmp_path)
    assert problems, "产物里有 data/ 必须报问题"
    assert "data" in problems[0]


def test_finds_shipped_user_data_under_resource_root(tmp_path):
    res = tmp_path / "_internal"
    (res / "data").mkdir(parents=True)
    problems = check_pack.find_shipped_user_data(tmp_path, res)
    assert problems, "资源根下出现 data/ 也要报"


def test_finds_secret_key_in_text_file(tmp_path):
    js = tmp_path / "app.js"
    js.write_text('const k = "sk-abcdefghijklmnopqrstuvwx";', encoding="utf-8")
    problems = check_pack.find_shipped_secrets(tmp_path)
    assert problems, "文本文件里出现 sk- Key 必须报问题"


def test_clean_artifact_passes(tmp_path):
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    assert check_pack.find_shipped_user_data(tmp_path, tmp_path) == []
    assert check_pack.find_shipped_secrets(tmp_path) == []


def test_engine_dir_is_not_scanned(tmp_path):
    """内置引擎目录体积大且含二进制，不参与凭证扫描。"""
    eng = tmp_path / "engine" / "chrome-win64"
    eng.mkdir(parents=True)
    (eng / "x.js").write_text('const k = "sk-abcdefghijklmnopqrstuvwx";', encoding="utf-8")
    assert check_pack.find_shipped_secrets(tmp_path) == []


def test_packaging_datas_excludes_user_data(tmp_path):
    """随包资产清单只有前端产物与扩展；用户数据目录 data/ 绝不入包。"""
    names = [dest for _src, dest in spec_common.datas(tmp_path)]
    assert names == ["frontend/dist", "extension"]
    assert not any("data" == n.split("/")[0] for n in names)
