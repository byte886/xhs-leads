"""R-6.48m：登录页注入选择器集中管理 + 拦截判定纯函数测试。

覆盖：
- decide_block 拦截判定全矩阵（组件内放行 / 遮罩拦 / 其他空白拦 / mask 优先）
- force_login JS 与常量表一致性（页面类名更新只改常量，JS 插值自动同步，这里防漂移）
- 事件集合完整嵌入
- 48j 坑回归：无换行拼接禁 // 行注释（吞 IIFE 尾段）、IIFE 必须闭合
- node --check 语法校验（node 不可用时 skip）
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from src import injections


class TestDecideBlock:
    """拦截判定矩阵：hits_panel=目标在登录组件内；hits_mask=命中遮罩 .reds-mask。"""

    @pytest.mark.parametrize(
        "hits_panel,hits_mask,expected",
        [
            (True, False, False),  # 组件内（扫码/输入框/tab）→ 放行
            (False, True, True),   # 点遮罩（空白）→ 拦截
            (False, False, True),  # 其他空白（body/背景）→ 拦截
            (True, True, True),    # mask 优先：即使命中组件也拦截（遮罩在组件外）
        ],
    )
    def test_decision_matrix(self, hits_panel, hits_mask, expected):
        assert injections.decide_block(hits_panel, hits_mask) is expected


class TestForceLoginSelectors:
    """选择器集中管理防漂移：JS 必须包含常量表当前值。"""

    def test_panel_selectors_embedded(self):
        js = injections.get("force_login")
        for sel in injections.LOGIN_PANEL_SELECTORS:
            assert sel in js, f"选择器 {sel} 未嵌入 force_login JS"

    def test_mask_selector_embedded(self):
        assert injections.LOGIN_MASK_SELECTOR in injections.get("force_login")

    def test_click_events_embedded(self):
        js = injections.get("force_login")
        for ev in injections.LOGIN_CLICK_EVENTS:
            assert ev in js
        for ev in injections.LOGIN_ESC_EVENTS:
            assert ev in js

    def test_js_uses_constant_values_not_drifted(self):
        js = injections.get("force_login")
        # 若 JS 里出现硬编码但常量已改，旧值残留说明生成未插值（漂移信号）
        assert ", '.container," not in js  # 常量 CSV 以常量拼接，不应出现游离旧写法


class TestJsSyntax:
    """注入 JS 语法与 48j 坑回归。"""

    @pytest.mark.parametrize("key", ["force_login", "login_fit"])
    def test_no_line_comments(self, key):
        js = injections.get(key)
        # 无换行拼接 + // 行注释会把后续代码连同 })(); 吞成注释（R-6.48j 根因）
        # 仅针对逐行拼接注入；shell_title 等三引号原始串有真换行，// 安全
        assert "//" not in js, f"{key} 含 // 行注释（无换行拼接下会吞 IIFE 尾段）"

    @pytest.mark.parametrize("key", ["force_login", "login_fit"])
    def test_iife_closed(self, key):
        js = injections.get(key)
        assert js.rstrip().endswith("})();"), f"{key} IIFE 未正确闭合"

    @pytest.mark.parametrize("key", ["force_login", "login_fit", "shell_title"])
    def test_node_syntax_check(self, key):
        if not shutil.which("node"):
            pytest.skip("node 不可用，跳过语法校验")
        js = injections.get(key)
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(js)
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, f"{key} node --check 失败: {r.stderr}"
        finally:
            os.unlink(path)
