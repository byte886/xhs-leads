"""search 流程级单测（R-6.53z3 拆分后：准备/单轮/循环/入口四层）。

search 是采集热路径但此前只有 `_should_stop_collect` 的纯逻辑用例——
崩溃重开、假死收尾、页面必关这三条编排语义没有守护。
"""
import asyncio

import pytest

from src import browser_client as bc


class _Page:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, js):
        return None

    async def bring_to_front(self):
        return None


def _run(coro):
    return asyncio.run(coro)


class TestCardTypeFilter:
    """R-6.77：提取层按「图文/视频」兜底过滤（平台筛选点击失败时仍保证结果正确）。"""

    def test_default_or_all_passes(self):
        note = {"type": "video"}
        assert bc._card_type_matches(note, None)
        assert bc._card_type_matches(note, "")
        assert bc._card_type_matches(note, "全部")

    def test_note_type_wanted(self):
        assert bc._card_type_matches({"type": "note"}, "图文")
        assert not bc._card_type_matches({"type": "video"}, "图文")
        # 卡片缺 type 字段时按图文处理（extract.js 只在有播放图标时才给 video）
        assert bc._card_type_matches({}, "图文")

    def test_video_type_wanted(self):
        assert bc._card_type_matches({"type": "video"}, "视频")
        assert not bc._card_type_matches({"type": "note"}, "视频")

    def test_unknown_want_type_passes(self):
        assert bc._card_type_matches({"type": "video"}, "其他")


class TestSearchRound:
    """单轮：提取→播报→判定→滚动；滚动阶段崩溃必须带归因标记。"""

    def test_progress_called_and_returns(self, monkeypatch):
        got = []

        async def fake_cdp(aw, *, timeout=None, label="", skip=False):
            return await aw

        async def fake_drain(page, items, seen, want_type=None):
            items.extend([{"feed_id": "a"}])
            return 1

        monkeypatch.setattr(bc, "cdp_call", fake_cdp)
        monkeypatch.setattr(bc, "_drain_page", fake_drain)
        monkeypatch.setattr(bc, "_should_stop_collect", lambda *a: (False, 0))
        items, seen = [], set()
        added, stale, done = _run(bc._search_round(
            _Page(), items, seen, 30, 0, None,
            lambda n, it=None: got.append((n, len(it))))
        )
        assert (added, stale, done) == (1, 0, False)
        assert got == [(1, 1)]  # 回调携带 items 引用（R-6.53v 抢救数据）

    def test_scroll_crash_is_tagged(self, monkeypatch):
        async def fake_cdp(aw, *, timeout=None, label="", skip=False):
            if label == "scroll_more":
                aw.close()  # 不 await，避免 RuntimeWarning
                raise RuntimeError("Target closed")
            return await aw

        async def fake_drain(page, items, seen, want_type=None):
            return 0

        monkeypatch.setattr(bc, "cdp_call", fake_cdp)
        monkeypatch.setattr(bc, "_drain_page", fake_drain)
        monkeypatch.setattr(bc, "_should_stop_collect", lambda *a: (False, 0))
        with pytest.raises(RuntimeError) as ei:
            _run(bc._search_round(_Page(), [], set(), 30, 0, None, None))
        assert getattr(ei.value, "_xhs_stage", "") == "滚动"


class TestSearchLoop:
    """循环：done 即退 / 假死收尾 / 崩溃重开计数 / 无法恢复返回 None。"""

    def test_done_exits_immediately(self, monkeypatch):
        calls = []

        async def fake_round(*a, **k):
            calls.append(1)
            return 3, 0, True

        monkeypatch.setattr(bc, "_search_round", fake_round)
        page = _Page()
        got_page, _stale = _run(bc._search_loop(
            page, "kw", {}, "default", [], set(), 30, 0, None, None))
        assert got_page is page
        assert len(calls) == 1

    def test_fake_dead_finishes_quietly(self, monkeypatch):
        async def fake_round(*a, **k):
            raise bc.CdpTimeoutError("CDP 无响应")

        monkeypatch.setattr(bc, "_search_round", fake_round)
        page = _Page()
        got_page, _ = _run(bc._search_loop(
            page, "kw", {}, "default", [], set(), 30, 0, None, None))
        assert got_page is page  # 假死只收尾，不判崩溃、不重开

    def test_crash_reopens_and_continues(self, monkeypatch):
        seq = ["crash", "ok"]
        recovered = []

        async def fake_round(page, *a, **k):
            if seq.pop(0) == "crash":
                raise RuntimeError("Page crashed")
            return 5, 0, True

        async def fake_recover(page, kw, f, acc, exc, kind, tries, max_tries):
            recovered.append((kind, tries))
            return _Page()

        monkeypatch.setattr(bc, "_search_round", fake_round)
        monkeypatch.setattr(bc, "_search_recover", fake_recover)
        got_page, _ = _run(bc._search_loop(
            _Page(), "kw", {}, "default", [], set(), 30, 0, None, None))
        assert got_page is not None
        assert recovered == [("采集页", 0)]  # 首次重开，tries=0

    def test_unrecoverable_returns_none(self, monkeypatch):
        async def fake_round(*a, **k):
            raise RuntimeError("Target closed")

        async def fake_recover(*a, **k):
            return None

        monkeypatch.setattr(bc, "_search_round", fake_round)
        monkeypatch.setattr(bc, "_search_recover", fake_recover)
        got_page, _ = _run(bc._search_loop(
            _Page(), "kw", {}, "default", [], set(), 30, 0, None, None))
        assert got_page is None


class TestSearchEntry:
    """入口：准备假死返回空结果 / 页面必关 / count 与 stopped 口径。"""

    def test_prepare_fake_dead_returns_empty(self, monkeypatch):
        async def fake_prep(kw, f, acc):
            return None

        monkeypatch.setattr(bc, "_search_prepare", fake_prep)
        r = _run(bc.search("翡翠", 5, should_stop=lambda: True))
        assert r == {"keyword": "翡翠", "count": 0, "items": [], "stopped": True,
                     "filter_warnings": []}

    def test_page_closed_and_count_reported(self, monkeypatch):
        closed = []
        page = _Page()

        async def fake_prep(kw, f, acc):
            return page

        async def fake_loop(page, kw, f, acc, items, seen, limit, stale, stop, prog):
            items.extend([{"feed_id": "a"}, {"feed_id": "b"}])
            return page, 0

        async def fake_close(p, acc):
            closed.append(p)

        monkeypatch.setattr(bc, "_search_prepare", fake_prep)
        monkeypatch.setattr(bc, "_search_loop", fake_loop)
        monkeypatch.setattr(bc, "_close_page_safe", fake_close)
        r = _run(bc.search("翡翠", 5))
        assert r["count"] == 2 and len(r["items"]) == 2 and r["stopped"] is False
        assert closed == [page]  # finally 必关采集页

    def test_unrecoverable_still_closes_nothing_new(self, monkeypatch):
        closed = []

        async def fake_prep(kw, f, acc):
            return _Page()

        async def fake_loop(page, kw, f, acc, items, seen, limit, stale, stop, prog):
            return None, 0  # 重开失败：旧页已在 _search_recover 内关闭

        async def fake_close(p, acc):
            closed.append(p)

        monkeypatch.setattr(bc, "_search_prepare", fake_prep)
        monkeypatch.setattr(bc, "_search_loop", fake_loop)
        monkeypatch.setattr(bc, "_close_page_safe", fake_close)
        r = _run(bc.search("翡翠", 5))
        assert r["count"] == 0
        assert closed == []  # 不重复关闭已关闭页面


class _SearchPage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.reloads = 0

    async def reload(self, timeout=None):
        self.reloads += 1


def _no_config(monkeypatch):
    monkeypatch.setattr(bc.config, "get_config", lambda: {})


def _passthrough_cdp(monkeypatch):
    async def fake_cdp(aw, *, timeout=None, label="", skip=False):
        return await aw

    monkeypatch.setattr(bc, "cdp_call", fake_cdp)


class TestPrepareSearchPage:
    """R-6.53z8：新页 → /login（已登录由平台自跳 explore）→ 点搜索框输入回车。

    三条守护：未登录直接抛错并保留页面；空壳只重试"再输入一次"，不 reload、
    不回退旧版搜索 URL；筛选步骤命中登录墙照样抛错。
    """

    def test_not_logged_in_raises_and_keeps_page(self, monkeypatch):
        page = _SearchPage()

        async def fake_page(_a="default"):
            return page

        async def fake_browse(p, acc):
            return False

        monkeypatch.setattr(bc, "_page", fake_page)
        monkeypatch.setattr(bc, "_ensure_browse_page", fake_browse)
        with pytest.raises(bc.BrowserLoginRequiredError):
            _run(bc._prepare_search_page("翡翠"))
        assert page.closed is False  # 保留页面供扫码/验证

    def test_second_typing_recovers(self, monkeypatch):
        page, waits, applied = _SearchPage(), [False, True], []

        async def fake_page(_a="default"):
            return page

        async def fake_browse(p, acc):
            return True

        async def fake_type(p, kw, acc):
            return waits.pop(0)

        async def fake_filters(p, f):
            applied.append(f)

        monkeypatch.setattr(bc, "_page", fake_page)
        monkeypatch.setattr(bc, "_ensure_browse_page", fake_browse)
        monkeypatch.setattr(bc, "_search_by_typing", fake_type)
        monkeypatch.setattr(bc, "_apply_filters", fake_filters)
        got = _run(bc._prepare_search_page("翡翠"))
        assert got is page and applied == [{}] and waits == []

    def test_search_fail_returns_none_not_recommend_feed(self, monkeypatch):
        """R-6.77：两次都进不了结果页 → 返回 None，绝不拿 explore 首页推荐流当结果。

        旧行为是「返回 page 继续采」：explore 首页本来就有 30 张卡片，于是用户看到
        「压根没有采集过程」却采回一堆与关键词无关的笔记（视频也混在里面）。
        """
        page = _SearchPage()

        async def fake_page(_a="default"):
            return page

        async def fake_browse(p, acc):
            return True

        async def fake_type(p, kw, acc):
            return False

        async def fake_filters(p, f):
            raise AssertionError("空壳不应进入筛选步骤")

        monkeypatch.setattr(bc, "_page", fake_page)
        monkeypatch.setattr(bc, "_ensure_browse_page", fake_browse)
        monkeypatch.setattr(bc, "_search_by_typing", fake_type)
        monkeypatch.setattr(bc, "_apply_filters", fake_filters)
        assert _run(bc._prepare_search_page("翡翠")) is None

    def test_filter_step_wall_raises(self, monkeypatch):
        page, waits = _SearchPage(), [(True, "完成验证")]

        async def fake_page(_a="default"):
            return page

        async def fake_browse(p, acc):
            return True

        async def fake_type(p, kw, acc):
            return True

        async def fake_wait(p, timeout_s=20):
            return waits.pop(0)

        async def fake_filters(p, f):
            return None

        monkeypatch.setattr(bc, "_page", fake_page)
        monkeypatch.setattr(bc, "_ensure_browse_page", fake_browse)
        monkeypatch.setattr(bc, "_search_by_typing", fake_type)
        monkeypatch.setattr(bc, "_wait_note_cards", fake_wait)
        monkeypatch.setattr(bc, "_apply_filters", fake_filters)
        monkeypatch.setattr(bc, "_handle_login_wall", lambda acc, wall: None)
        with pytest.raises(bc.BrowserLoginRequiredError):
            _run(bc._prepare_search_page("翡翠", {"sort": "综合"}))


class TestNoteTypeFilter:
    """R-6.77：笔记类型兜底过滤。

    平台筛选靠点击页面选项实现，点不中就一个筛选都没生效（用户实测「选图文却采到视频」）。
    提取层这道兜底保证：即使筛选失败，交出去的卡片类型也一定是用户选的那种。
    """

    def test_default_passes_everything(self):
        assert bc._card_type_matches({"type": "video"}, "") is True
        assert bc._card_type_matches({"type": "note"}, "全部") is True
        assert bc._card_type_matches({"type": "video"}, "全部") is True

    def test_tuwen_excludes_video(self):
        assert bc._card_type_matches({"type": "note"}, "图文") is True
        assert bc._card_type_matches({"type": "video"}, "图文") is False

    def test_video_only_keeps_video(self):
        assert bc._card_type_matches({"type": "video"}, "视频") is True
        assert bc._card_type_matches({"type": "note"}, "视频") is False

    def test_drain_page_drops_video_and_ads(self, monkeypatch):
        cards = [
            {"id": "a" * 24, "type": "note"},
            {"id": "b" * 24, "type": "video"},
            {"id": "promo-uuid", "type": "note"},  # 非 24 位 hex：广告位
        ]

        class _P:
            async def evaluate(self, js):
                return cards

        async def fake_inject(page, name):
            return None

        monkeypatch.setattr(bc.injections, "inject_once", fake_inject)
        items, seen = [], set()
        added = _run(bc._drain_page(_P(), items, seen, "图文"))
        assert added == 1
        assert [it["type"] for it in items] == ["note"]


class TestSearchFailGuard:
    """R-6.77：搜索失败不能退回首页推荐流。"""

    def test_search_returns_zero_when_prepare_none(self, monkeypatch):
        async def fake_prepare(*a, **kw):
            return None

        monkeypatch.setattr(bc, "_search_prepare", fake_prepare)
        got = _run(bc.search("翡翠", limit=10))
        assert got["count"] == 0 and got["items"] == []
        assert got["filter_warnings"] == []


class TestFilterWarn:
    """R-6.81：筛选未生效要给用户能自助排查的提示，位置距离组必须点明定位。"""

    def test_location_group_explains_geolocation(self):
        for val in ("同城", "附近"):
            w = bc._filter_warn("位置距离", val)
            assert w["label"] == "位置距离" and w["value"] == val
            assert "定位" in w["hint"]
            assert val in w["hint"]

    def test_other_group_generic_without_location_blame(self):
        w = bc._filter_warn("笔记类型", "图文")
        assert "定位" not in w["hint"]
        assert "笔记类型" in w["hint"] and "图文" in w["hint"]
