"""评论采集的前置条件（R-6.54，用户 2026-11-08 拍板）。

钉住四条：
1. 有活动搜索页 → 直接点卡片，不做多余搜索；
2. 没有活动搜索页但有 keyword → 按关键词重开一次搜索页（keep_page）再继续；
3. 重开时必须带上原采集的筛选条件（否则综合排序结果里没有原卡片，误报"已不在结果里"）；
4. 既没有页、也无法打开 → 抛 ClientCollectError，绝不静默返回空。
"""
import asyncio

from src import browser_client as bc


class _FakePage:
    def __init__(self):
        self.url = "https://www.xiaohongshu.com/search_result?keyword=x"

    def is_closed(self):
        return False

    async def evaluate(self, *_args, **_kwargs):
        return None


def test_no_page_no_keyword_raises():
    bc._ACTIVE_PAGES.pop("default", None)
    try:
        asyncio.run(bc.clients("nid", keyword=""))
    except bc.ClientCollectError as e:
        assert "搜索结果页" in str(e)
    else:  # pragma: no cover - 走到这里说明被静默跳过了
        raise AssertionError("无搜索页时不应静默返回")


def test_keyword_reopens_search_then_collects(monkeypatch):
    calls = []

    async def fake_search(keyword, limit=1, account_id="default", keep_page=False,
                          filters=None, **_):
        calls.append((keyword, keep_page, filters))
        bc._ACTIVE_PAGES[account_id] = _FakePage()
        return {"count": 1}

    async def fake_open(page, note_id):
        calls.append(("open", note_id))
        return True

    async def fake_drain(page):
        return [{"uid": "u1"}]

    async def fake_back(page):
        return None

    async def _no_sleep(*_):
        return None

    monkeypatch.setattr(bc, "search", fake_search)
    monkeypatch.setattr(bc, "_open_note_card", fake_open)
    monkeypatch.setattr(bc, "_drain_comments", fake_drain)
    monkeypatch.setattr(bc, "_back_to_list", fake_back)
    monkeypatch.setattr(bc.asyncio, "sleep", _no_sleep)
    bc._ACTIVE_PAGES.pop("default", None)

    # 采集时的筛选条件必须原样带到重开的搜索页
    filters = {"note_type": "图文", "sort": "最新"}
    users = asyncio.run(bc.clients("nid", keyword="翡翠手串", filters=filters))
    assert users == [{"uid": "u1"}]
    assert calls[0] == ("翡翠手串", True, filters)  # 只搜索、保留页面、带原筛选
    assert calls[1] == ("open", "nid")


def test_card_missing_raises(monkeypatch):
    async def fake_open(page, note_id):
        return False

    bc._ACTIVE_PAGES["default"] = _FakePage()
    monkeypatch.setattr(bc, "_open_note_card", fake_open)
    try:
        asyncio.run(bc.clients("nid"))
    except bc.ClientCollectError as e:
        assert "搜索结果" in str(e)
    else:  # pragma: no cover
        raise AssertionError("卡片没命中时应报错而非返回空")
