"""扩展 ID 选择单测。

Windows 实测：目录型扩展的 ID 由安装路径派生，但 Chromium 在各平台的路径规范化
口径不同——推导值 `befemk...` 与运行时真实 ID `angbdp...` 不一致，消息因此打到
不存在的接收方（"Receiving end does not exist"），原子建窗被静默降级。这组测试守住
「运行时真相优先」这条规则，避免以后有人把运行时探测当冗余删掉。
"""
from src import shell_window as sw


class _Sw:
    """假 service worker：只需要 url 属性。"""

    def __init__(self, url):
        self.url = url


class _Ctx:
    def __init__(self, workers=None):
        self.service_workers = workers if workers is not None else []


class _CtxNoSw:
    """模拟 Playwright 未暴露 service_workers 的版本。"""

    @property
    def service_workers(self):
        raise RuntimeError("service_workers unsupported")


def test_runtime_ids_from_sw_url():
    ctx = _Ctx([_Sw("chrome-extension://abcdefghijklmnop/background.js")])
    assert sw._runtime_ext_ids(ctx) == ["abcdefghijklmnop"]


def test_runtime_ids_dedup_and_ignore_non_extension():
    ctx = _Ctx([
        _Sw("chrome-extension://abcdefghijklmnop/background.js"),
        _Sw("chrome-extension://abcdefghijklmnop/other.js"),
        _Sw("https://www.xiaohongshu.com/"),
        _Sw("http://127.0.0.1:8791/"),
    ])
    assert sw._runtime_ext_ids(ctx) == ["abcdefghijklmnop"]


def test_runtime_ids_empty_when_no_sw():
    assert sw._runtime_ext_ids(_Ctx()) == []


def test_runtime_ids_swallow_broken_ctx():
    assert sw._runtime_ext_ids(_CtxNoSw()) == []


def test_pick_keeps_computed_when_runtime_matches():
    assert sw._pick_ext_id("aaa", ["aaa", "bbb"]) == "aaa"


def test_pick_uses_runtime_when_single_and_mismatched():
    # Windows 真实值：推导 befemkmndknceacoakdoiaihgfhelmcg → 实际 angbdpfjbfcdnjkbdmaeenligaajpkic
    assert sw._pick_ext_id(
        "befemkmndknceacoakdoiaihgfhelmcg",
        ["angbdpfjbfcdnjkbdmaeenligaajpkic"],
    ) == "angbdpfjbfcdnjkbdmaeenligaajpkic"


def test_pick_keeps_computed_when_ambiguous():
    # 多个运行时扩展且都不等于推导值：无法判断该用哪个，保守沿用推导（失败则回退建窗）
    assert sw._pick_ext_id("aaa", ["bbb", "ccc"]) == "aaa"


def test_pick_computed_when_no_runtime():
    assert sw._pick_ext_id("aaa", []) == "aaa"
