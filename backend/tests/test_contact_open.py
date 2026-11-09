"""open_contact 单测：主页 → 点聊天按钮 → 落到 /chat?openUid 私信会话页（不启动真实 Chrome）。"""
import asyncio

import pytest

from src import browser_client as bc
from tests.fake_playwright import FakeContext, FakePage

PROFILE = "https://www.xiaohongshu.com/user/profile/5663fcd80bf90c4d12a73ccc"
CHAT = "https://www.xiaohongshu.com/chat?openUid=5663fcd80bf90c4d12a73ccc"
IM_BTN = "button.xhs-user-im-btn"


def _run(coro):
    return asyncio.run(coro)


def _make_ctx(*, btn_ready=True, spawn_chat=True, pre_chat=False):
    """构造假 context；profile 页记录在 holder 里，点击可选择是否产生 chat 页。"""
    ctx = FakeContext()
    if pre_chat:
        ctx.pages.append(FakePage(CHAT))
    holder = {"profile": None}
    orig_new_page = ctx.new_page

    async def new_page():
        page = await orig_new_page()
        page.set_selector(IM_BTN, btn_ready)
        if spawn_chat:
            async def on_click(_selector):
                ctx.pages.append(FakePage(CHAT))
            page._on_click = on_click
        holder["profile"] = page
        return page

    ctx.new_page = new_page
    return ctx, holder


def _patch_ctx(monkeypatch, ctx):
    async def fake_ctx(account_id="default"):
        return ctx
    monkeypatch.setattr(bc, "_ctx", fake_ctx)


def test_open_contact_success(monkeypatch):
    ctx, holder = _make_ctx()
    _patch_ctx(monkeypatch, ctx)

    assert _run(bc.open_contact(PROFILE)) == CHAT
    assert holder["profile"].clicked == [IM_BTN]


def test_open_contact_reuses_existing_chat(monkeypatch):
    # 同 uid 会话页已存在（平台复用 target）：不新开也应识别并聚焦
    ctx, holder = _make_ctx(spawn_chat=False, pre_chat=True)
    _patch_ctx(monkeypatch, ctx)

    assert _run(bc.open_contact(PROFILE)) == CHAT
    assert holder["profile"].clicked == [IM_BTN]


def test_open_contact_invalid_profile(monkeypatch):
    ctx, _holder = _make_ctx()
    _patch_ctx(monkeypatch, ctx)

    with pytest.raises(bc.ContactOpenError):
        _run(bc.open_contact("https://www.xiaohongshu.com/explore/abc"))


def test_open_contact_button_not_ready(monkeypatch):
    ctx, holder = _make_ctx(btn_ready=False)
    _patch_ctx(monkeypatch, ctx)

    with pytest.raises(bc.ContactOpenError):
        _run(bc.open_contact(PROFILE))
    assert holder["profile"].clicked == []


def test_open_contact_no_chat_after_click(monkeypatch):
    ctx, holder = _make_ctx(spawn_chat=False)
    _patch_ctx(monkeypatch, ctx)

    with pytest.raises(bc.ContactOpenError):
        _run(bc.open_contact(PROFILE))
    assert holder["profile"].clicked == [IM_BTN]
