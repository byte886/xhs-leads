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


class TestSearchRound:
    """单轮：提取→播报→判定→滚动；滚动阶段崩溃必须带归因标记。"""

    def test_progress_called_and_returns(self, monkeypatch):
        got = []

        async def fake_cdp(aw, *, timeout=None, label="", skip=False):
            return await aw

        async def fake_drain(page, items, seen):
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

        async def fake_drain(page, items, seen):
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
        assert r == {"keyword": "翡翠", "count": 0, "items": [], "stopped": True}

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
