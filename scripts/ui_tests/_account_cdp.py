# -*- coding: utf-8 -*-
"""R-6.57 多账号 UI 测试连接助手。

背景（2026-11-08 实测）：多账号引擎若共用同一个 `--remote-debugging-port`，
Chromium 绑定失败是**静默跳过、不报错**——第二个账号能正常启动、页面也在，
但该端口上只有第一个账号的 target。此时任何测试 `connect_over_cdp(9223)`
操作的都是第一个账号的登录态与数据，串号且难以察觉。

现在每账号独占一个端口（default 沿用 9223，其余从 9224 起分配并持久化在
accounts.json）。测试脚本按 account_id 连，不再硬编码端口号。

用法：
    from _account_cdp import account_port, connect_account, pick_page

    b = connect_account("default")           # 连 default 账号（9223）
    pg = pick_page(b, "8791")                # 从该账号窗口里挑出 APP 页
    ...
    b.close()

    b2 = connect_account("test_a")           # 连 test_a 账号（9224）
"""

import json
import urllib.request

BACKEND = "http://127.0.0.1:8791"


def accounts(base: str = BACKEND, timeout: float = 10.0) -> dict:
    """取后端账号列表原始响应（含 accounts / port_map / active）。"""
    with urllib.request.urlopen(f"{base}/api/accounts", timeout=timeout) as r:
        return json.load(r)


def account_port(account_id: str = "default", base: str = BACKEND, timeout: float = 10.0) -> int:
    """该账号的 CDP 调试端口。后端不可达时 default 退回 9223。"""
    try:
        data = accounts(base, timeout)
    except Exception:  # noqa: BLE001
        if account_id == "default":
            return 9223
        raise
    port = (data.get("port_map") or {}).get(account_id)
    if port is None:
        raise KeyError(f"账号 {account_id} 不存在或未分配端口；已有 {list((data.get('port_map') or {}).keys())}")
    return int(port)


def connect_account(account_id: str = "default", base: str = BACKEND, timeout: float = 10.0):
    """连到该账号的调试端口，返回 Browser。调用方负责 close()。"""
    from playwright.sync_api import sync_playwright

    port = account_port(account_id, base, timeout)
    pw = sync_playwright().start()
    try:
        return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception:
        pw.stop()
        raise


def pick_page(browser, url_part: str):
    """从浏览器所有上下文/页面里挑第一个 URL 含 url_part 的页面（找不到返回 None）。"""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if url_part in (pg.url or ""):
                return pg
    return None


def require_page(browser, url_part: str):
    """同 pick_page，但找不到直接抛错（避免测试静默跑在空页上）。"""
    pg = pick_page(browser, url_part)
    if pg is None:
        raise RuntimeError(f"该账号窗口里找不到 URL 含「{url_part}」的页面")
    return pg
