"""采集任务状态机单测（R-6.53z 逐条流水线 + 四分支收尾 + 框架级停止）。

覆盖 SOP §二 S2/S3 与 TS8/TS9/TS10 的单元层。硬约束：任何路径都不许漏落
status——running 是 mirror 切窗、keeper 暂停 tab2 治理、Tab2 关闭三处唯一判据。
"""
import asyncio

import pytest

from src import server


def _req(limit: int = 20, fast: bool = False) -> "server.SearchReq":
    return server.SearchReq(keyword="翡翠", limit=limit, fast=fast)


def _state(**kw) -> dict:
    st = {
        "status": "running",
        "phase": "enrich",
        "stop_event": False,
        "saved": 0,
        "collected": 0,
        "partial_items": [],
        "note": "",
    }
    st.update(kw)
    return st


class TestFinalize:
    """`_collect_finalize` 四分支（S3 前四行 + 停止行）。"""

    def test_stopped_with_data(self):
        st = _state(saved=3, stop_event=True)
        server._collect_finalize(st, _req(limit=20), {"stopped": True})
        assert st["status"] == "stopped"
        assert st["note"] == "已停止：采集到 3 条"

    def test_stopped_without_data(self):
        st = _state(saved=0, stop_event=True)
        server._collect_finalize(st, _req(limit=20), {"stopped": True})
        assert st["status"] == "stopped"
        assert st["note"] == "已停止（未采到结果）"

    def test_reached_limit(self):
        st = _state(saved=5)
        server._collect_finalize(st, _req(limit=5), {})
        assert st["status"] == "done"
        assert st["note"] == "采集完成：5 条已按评分排序"

    def test_platform_loaded_fewer(self):
        st = _state(saved=3)
        server._collect_finalize(st, _req(limit=20), {})
        assert st["status"] == "done"
        assert "平台仅加载 3 条" in st["note"]

    def test_all_candidates_rejected(self):
        st = _state(saved=0)
        server._collect_finalize(st, _req(limit=20), {})
        assert st["status"] == "done"
        assert "未采集到有效数据" in st["note"]


class TestFinalizeEmpty:
    """`_collect_finalize_empty`：搜索阶段 0 候选（S3 最后一行 done 变体）。"""

    def test_stopped_by_user(self):
        st = _state(stop_event=True)
        server._collect_finalize_empty(st, {"stopped": True})
        assert st["status"] == "stopped"
        assert st["note"] == "已停止（未采到结果）"

    def test_platform_empty_is_failure_hint(self):
        st = _state()
        server._collect_finalize_empty(st, {})
        assert st["status"] == "done"
        assert "未采集到数据" in st["note"]
        assert st["phase"] == "done"


class _FakeTask:
    def __init__(self) -> None:
        self.cancel_calls = 0
        self._done = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancel_calls += 1


def _run_takeover(monkeypatch) -> None:
    """跑一次顶掉动作；把「等收尾」和「复位采集窗」换成空实现，只验取消/标停语义。"""
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(server, "_await_orphans", _noop)
    monkeypatch.setattr(server, "_reset_collect_windows", _noop)
    asyncio.run(server._stop_running_collect_tasks())


class TestStopRunningTasks:
    """R-6.53v：新任务顶旧任务 = stop_event + task.cancel() 双动作。"""

    def test_running_task_gets_both_signals(self, monkeypatch):
        task = _FakeTask()
        st = _state(task=task)
        monkeypatch.setattr(server, "_COLLECT_TASKS", {"t1": st})
        _run_takeover(monkeypatch)
        assert st["stop_event"] is True
        assert task.cancel_calls == 1

    def test_finished_task_untouched(self, monkeypatch):
        task = _FakeTask()
        task._done = True
        st = _state(task=task, status="done")
        monkeypatch.setattr(server, "_COLLECT_TASKS", {"t1": st})
        _run_takeover(monkeypatch)
        assert st["stop_event"] is False
        assert task.cancel_calls == 0

    def test_running_without_task_ref(self, monkeypatch):
        st = _state()
        monkeypatch.setattr(server, "_COLLECT_TASKS", {"t1": st})
        _run_takeover(monkeypatch)  # 不抛
        assert st["stop_event"] is True


class _FakeCon:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _ctx(con, fast: bool = False) -> dict:
    return {
        "req": _req(fast=fast),
        "account_id": "default",
        "con": con,
        "cache": {},
        "now": "2026-11-06T00:00:00",
    }


class TestCollectOne:
    """`_collect_one`：一条一补全、一评分、一 commit、一播报。"""

    def test_enrich_failure_still_scores_with_coarse_data(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("detail 超时")

        seen = []

        def fake_rank(con, n, keyword, now, account_id="default"):
            seen.append(n)
            return True

        monkeypatch.setattr(server, "get_feed_detail", boom)
        monkeypatch.setattr(server, "get_user_profile", boom)
        monkeypatch.setattr(server, "_rank_and_insert", fake_rank)
        con, st = _FakeCon(), _state()
        asyncio.run(server._collect_one({"feed_id": "f1"}, st, _ctx(con)))
        assert st["saved"] == 1 and st["collected"] == 1
        assert st["note"] == "已采集 1 条"
        assert con.commits == 1
        assert seen == [{"feed_id": "f1"}]  # 用搜索页粗数据评分
        assert st["partial_items"] == [{"feed_id": "f1"}]

    def test_rejected_candidate_not_committed(self, monkeypatch):
        async def ok_detail(item, account_id):
            return {**item, "title": "补全后"}

        async def ok_profile(item, cache, account_id):
            return item

        monkeypatch.setattr(server, "get_feed_detail", ok_detail)
        monkeypatch.setattr(server, "get_user_profile", ok_profile)
        monkeypatch.setattr(server, "_rank_and_insert", lambda *a, **k: False)
        con, st = _FakeCon(), _state()
        asyncio.run(server._collect_one({"feed_id": ""}, st, _ctx(con)))
        assert st["saved"] == 0
        assert con.commits == 0  # 拒收行不入库、不提交（R-6.53x）

    def test_fast_mode_skips_detail(self, monkeypatch):
        calls = []

        async def fake_fast(n, cache, account_id="default"):
            calls.append(n)
            return None

        monkeypatch.setattr(server, "_enrich_fast", fake_fast)
        monkeypatch.setattr(server, "_rank_and_insert", lambda *a, **k: True)
        con, st = _FakeCon(), _state()
        asyncio.run(server._collect_one({"feed_id": "f2"}, st, _ctx(con, fast=True)))
        assert calls == [{"feed_id": "f2"}]
        assert st["saved"] == 1 and con.commits == 1


class TestOrphanTakeover:
    """R-6.53z4：新任务顶掉旧任务时，必须和「点停止」一样复位采集窗。"""

    def test_takeover_cancels_old_and_recovers_window(self, monkeypatch):
        async def scenario():
            recovered = []

            async def _fake_recover(account_id):
                recovered.append(account_id)

            monkeypatch.setattr(server, "_recover_collect_window", _fake_recover)
            started = asyncio.Event()

            async def _long_running():
                started.set()
                await asyncio.sleep(30)

            task = asyncio.create_task(_long_running())
            await started.wait()
            old = {"status": "running", "account_id": "collect_a", "stop_event": False, "task": task}
            server._COLLECT_TASKS["tid_old"] = old
            try:
                await server._stop_running_collect_tasks()
            finally:
                server._COLLECT_TASKS.clear()

            assert old["stop_event"] is True, "旧任务必须先被标停"
            assert task.done(), "旧任务必须被 cancel 且已收尾"
            assert recovered == ["collect_a"], "被顶掉的账号必须复位采集窗（与 collect_stop 同语义)"

        asyncio.run(scenario())

    def test_takeover_without_running_task_is_noop(self, monkeypatch):
        async def scenario():
            recovered = []

            async def _fake_recover(account_id):
                recovered.append(account_id)

            monkeypatch.setattr(server, "_recover_collect_window", _fake_recover)
            server._COLLECT_TASKS["tid_done"] = {"status": "done", "account_id": "a1"}
            try:
                await server._stop_running_collect_tasks()
            finally:
                server._COLLECT_TASKS.clear()
            assert recovered == [], "没有在跑的任务就不该去动采集窗"

        asyncio.run(scenario())
