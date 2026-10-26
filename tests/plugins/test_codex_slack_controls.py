"""Session metadata controls and durable pause semantics, including delivery races."""

import asyncio
import sqlite3
import threading
from dataclasses import replace

import pytest

from tests.plugins.test_codex_slack_plugin import (
    plugin, _manager, _binding, _event, _activity, _summary, FakeSession, TurnResult,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [False, True])
async def test_title_reads_live_name_and_renames_without_starting_work(plugin, tmp_path, paused):
    summary = _summary(cwd=tmp_path / "project", title="Original title")
    manager = _manager(plugin, tmp_path, threads=[summary])
    binding = _binding(plugin, manager)
    manager.store.pause_sync(binding, paused)
    await manager.handle_event(_event("!title   ", thread_id=binding.thread_ts))
    assert manager.gateway.adapter.client.messages[-1]["text"] == "当前标题：Original title"
    assert not manager.catalog.mutations

    await manager.handle_event(_event("!TITLE Odoo  &amp; &lt;Stock&gt;", thread_id=binding.thread_ts))
    assert manager.catalog.read_thread(binding.codex_thread_id).title == "Odoo  & <Stock>"
    assert manager.catalog.mutations == [("rename", binding.codex_thread_id, "Odoo  & <Stock>")]
    assert manager.gateway.adapter.client.messages[-1]["text"] == "标题已修改：Odoo  &amp; &lt;Stock&gt;"
    assert manager.gateway.adapter.client.messages[-1]["thread_ts"] == binding.thread_ts

    # A local client can rename the same session; reads must not use a Slack cache.
    manager.catalog.threads = [replace(summary, title="Changed locally")]
    await manager.handle_event(_event("!title", thread_id=binding.thread_ts))
    assert manager.gateway.adapter.client.messages[-1]["text"] == "当前标题：Changed locally"
    assert not manager._runtimes
    assert not manager.gateway.adapter.sent
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused == paused


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "write", "scope"])
async def test_title_failure_does_not_change_name_or_start_work(plugin, tmp_path, failure):
    summary = _summary(cwd=tmp_path / "project", title="Original title")
    manager = _manager(plugin, tmp_path, threads=[summary])
    binding = _binding(plugin, manager)

    def fail(*args):
        raise RuntimeError("offline")

    if failure == "read":
        manager.catalog.read_thread = fail
    elif failure == "write":
        manager.catalog.set_thread_name = fail
    else:
        manager.config = replace(manager.config, allowed_roots=(manager.config.default_cwd,))
        manager.catalog.threads = [replace(summary, cwd=str(tmp_path / "other"))]
    await manager.handle_event(_event("!title New title", thread_id=binding.thread_ts))
    assert "无法修改标题" in manager.gateway.adapter.client.messages[-1]["text"]
    assert manager.catalog.threads[0].title == "Original title"
    assert not manager.catalog.mutations
    assert not manager._runtimes


@pytest.mark.asyncio
async def test_stop_is_durable_and_resume_discards_paused_history(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("old", "working")
    await manager._sync_binding(binding)
    await manager.handle_event(_event("!stop", thread_id=binding.thread_ts))
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    assert manager.catalog.interrupts == []
    assert manager.catalog.activity["turns"][0]["status"] == "inProgress"
    assert not manager.store.in_channel(binding.channel_id)

    restarted = _manager(plugin, tmp_path)
    restarted.config = manager.config
    restarted.gateway = manager.gateway
    restarted.catalog.activity = _activity("paused", "do not replay", "completed")
    count = len(restarted.gateway.adapter.client.messages)
    await restarted._sync_binding(binding)
    assert len(restarted.gateway.adapter.client.messages) == count
    assert not restarted.gateway.adapter.sent
    await restarted.handle_event(_event("some work", thread_id=binding.thread_ts))
    assert not restarted._runtimes
    assert "!resume" in restarted.gateway.adapter.client.messages[-1]["text"]

    await restarted.handle_event(_event("!resume", thread_id=binding.thread_ts))
    await restarted._sync_binding(binding)
    assert not restarted.gateway.adapter.sent
    restarted.catalog.activity = _activity("new", "new result", "completed")
    await restarted._sync_binding(binding)
    assert [m["text"] for m in restarted.gateway.adapter.sent] == ["new result"]


@pytest.mark.asyncio
async def test_stop_cancels_poll_result_already_in_flight(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    entered, release = threading.Event(), threading.Event()

    def read(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _activity("inflight", "must not deliver", "completed")

    manager.catalog.read_activity = read
    poll = asyncio.create_task(manager._sync_binding(binding))
    assert await asyncio.to_thread(entered.wait, 5)
    stop = asyncio.create_task(manager.stop(binding))
    await asyncio.sleep(0)
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    release.set()
    await asyncio.gather(poll, stop)
    assert len(manager.gateway.adapter.client.messages) == 1  # stop acknowledgement only
    assert not manager.gateway.adapter.sent


@pytest.mark.asyncio
async def test_stop_drains_pending_delivery_before_acknowledging(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("done", "answer", "completed")
    entered, release = asyncio.Event(), asyncio.Event()
    post = manager._post_message

    async def slow_post(*args, **kwargs):
        entered.set()
        await release.wait()
        return await post(*args, **kwargs)

    manager._post_message = slow_post
    poll = asyncio.create_task(manager._sync_binding(binding))
    await entered.wait()
    stop = asyncio.create_task(manager.stop(binding))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.gather(poll, stop)
    assert "已暂停" in manager.gateway.adapter.client.messages[-1]["text"]
    assert not manager.gateway.adapter.sent
    count = len(manager.gateway.adapter.client.messages)
    await manager._sync_binding(binding)
    assert len(manager.gateway.adapter.client.messages) == count


@pytest.mark.asyncio
async def test_stop_still_pauses_if_daemon_is_unavailable(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)

    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    manager.catalog.read_activity = fail
    await manager.stop(binding)
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    assert "当前 Codex 任务继续运行" in manager.gateway.adapter.client.messages[-1]["text"]
    await manager.resume_sync(binding)
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    assert "恢复失败" in manager.gateway.adapter.client.messages[-1]["text"]


@pytest.mark.asyncio
async def test_mode_controls_never_become_prompts_or_resume_paused_sync(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    await manager.stop(binding)
    for command, mode in (("!mode plan", "plan"), ("!mode default", "default")):
        await manager.handle_event(_event(command, thread_id=binding.thread_ts))
        runtime = manager._runtimes[binding.codex_thread_id]
        assert runtime.session.mode == mode
        assert not runtime.worker
        assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    runtime.session.mode = "plan"  # Local client changed it; query must re-read.
    await manager.handle_event(_event("!mode", thread_id=binding.thread_ts))
    assert "Plan" in manager.gateway.adapter.client.messages[-1]["text"]
    await manager.handle_event(_event("!mode nonsense", thread_id=binding.thread_ts))
    assert "用法" in manager.gateway.adapter.client.messages[-1]["text"]
    assert not manager.gateway.adapter.sent


@pytest.mark.asyncio
async def test_mode_switch_refuses_active_worker(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    runtime = manager._runtime(binding)
    runtime.active = True
    await manager.mode(binding, "default")
    assert "等待结束" in manager.gateway.adapter.client.messages[-1]["text"]
    assert runtime.session is None
    runtime.active = False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "exception"])
@pytest.mark.parametrize("resume_running", [False, True])
async def test_stop_clears_queue_keeps_running_turn_and_gates_delivery(plugin, tmp_path, outcome, resume_running):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("running", "working")
    entered, release = threading.Event(), threading.Event()
    prompts = []

    class RunningSession(FakeSession):
        def run_turn(self, text):
            prompts.append(text)
            entered.set()
            assert release.wait(5)
            if outcome == "exception":
                raise RuntimeError("failed in background")
            return TurnResult(thread_id=binding.codex_thread_id, turn_id="running",
                              final_text="finished in background",
                              error="failed in background" if outcome == "failed" else None)

    manager.session_factory = RunningSession
    await manager.handle_event(_event("A", thread_id=binding.thread_ts))
    runtime = manager._runtimes[binding.codex_thread_id]
    worker = runtime.worker
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        session = runtime.session
        for message in ("B", "C"):
            await manager.handle_event(_event(message, thread_id=binding.thread_ts))
        generation = runtime.progress_generation
        await manager.handle_event(_event("!stop", thread_id=binding.thread_ts))
        assert "清空 2 条排队消息" in manager.gateway.adapter.client.messages[-1]["text"]
        assert not runtime.queue
        assert runtime.active and not worker.done()
        assert not runtime.stopping and not session.interrupted and not session.closed
        assert manager.catalog.interrupts == []
        await manager.handle_event(_event("D", thread_id=binding.thread_ts))
        assert "!resume" in manager.gateway.adapter.client.messages[-1]["text"]
        updates = len(manager.gateway.adapter.client.updates)
        await manager._update_progress(runtime, "suppressed")
        assert len(manager.gateway.adapter.client.updates) == updates

        if resume_running:
            await manager.handle_event(_event("!resume", thread_id=binding.thread_ts))
            await manager._update_progress(runtime, "stale", generation=generation)
            assert len(manager.gateway.adapter.client.updates) == updates
            await manager._update_progress(runtime, "live")
            assert manager.gateway.adapter.client.updates[-1]["text"] == "live"
        count = len(manager.gateway.adapter.client.messages)
        release.set()
        await worker
        assert prompts == ["A"]
        if resume_running:
            if outcome == "completed":
                assert manager.gateway.adapter.sent[-1]["text"] == "finished in background"
            else:
                assert "failed in background" in manager.gateway.adapter.client.messages[-1]["text"]
        else:
            assert len(manager.gateway.adapter.client.updates) == updates
            assert len(manager.gateway.adapter.client.messages) == count
            assert not manager.gateway.adapter.sent
            manager.catalog.activity = _activity("running", "finished in background", "completed")
            await manager.handle_event(_event("!resume", thread_id=binding.thread_ts))
            await manager._sync_binding(binding)
            assert not manager.gateway.adapter.sent
        assert not runtime.queue
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_discards_input_popped_before_session_start_even_after_resume(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    entered, release = asyncio.Event(), asyncio.Event()
    ensure_session = manager._ensure_session

    async def slow_start(runtime):
        entered.set()
        await release.wait()
        return await ensure_session(runtime)

    manager._ensure_session = slow_start
    await manager.enqueue_turn(binding, "not started", "U_ME")
    runtime = manager._runtimes[binding.codex_thread_id]
    worker = runtime.worker
    try:
        await entered.wait()
        await manager.handle_event(_event("!stop", thread_id=binding.thread_ts))
        await manager.handle_event(_event("!resume", thread_id=binding.thread_ts))
        release.set()
        await worker
        assert not manager.gateway.adapter.sent
        assert not manager.gateway.adapter.client.updates
        assert not runtime.active
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_drains_worker_final_status_then_suppresses_answer(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    entered, release = asyncio.Event(), asyncio.Event()
    edit = manager._edit_message

    async def slow_edit(*args, **kwargs):
        entered.set()
        await release.wait()
        return await edit(*args, **kwargs)

    manager._edit_message = slow_edit
    await manager.enqueue_turn(binding, "work", "U_ME")
    runtime = manager._runtimes[binding.codex_thread_id]
    worker = runtime.worker
    stop = None
    try:
        await entered.wait()
        stop = asyncio.create_task(manager.stop(binding))
        await asyncio.sleep(0)
        assert manager.store.by_codex(binding.codex_thread_id).sync_paused
        assert not stop.done()
        release.set()
        await asyncio.gather(worker, stop)
        assert "已暂停" in manager.gateway.adapter.client.messages[-1]["text"]
        assert not manager.gateway.adapter.sent
    finally:
        release.set()
        await asyncio.gather(worker, *([stop] if stop else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_resume_skips_completed_turn_whose_worker_result_is_pending(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)
    entered, release = threading.Event(), threading.Event()
    reading, finish_read = threading.Event(), threading.Event()

    class RunningSession(FakeSession):
        def run_turn(self, text):
            entered.set()
            assert release.wait(5)
            return TurnResult(thread_id=binding.codex_thread_id, turn_id="done", final_text="old result")

    def read(*args, **kwargs):
        reading.set()
        assert finish_read.wait(5)
        return _activity("done", "old result", "completed")

    manager.session_factory = RunningSession
    manager.catalog.read_activity = read
    await manager.enqueue_turn(binding, "work", "U_ME")
    runtime = manager._runtimes[binding.codex_thread_id]
    worker = runtime.worker
    resume = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await manager.stop(binding)
        resume = asyncio.create_task(manager.resume_sync(binding))
        assert await asyncio.to_thread(reading.wait, 5)
        release.set()  # Worker finishes while Resume owns the delivery lock.
        finish_read.set()
        await asyncio.gather(worker, resume)
        assert not manager.gateway.adapter.sent
        assert not manager.gateway.adapter.client.updates
        assert manager.store.progress(binding.codex_thread_id, "done")["completed"]
    finally:
        release.set()
        finish_read.set()
        await asyncio.gather(worker, *([resume] if resume else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_resume_button_restores_paused_binding_without_new_thread(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    binding = _binding(plugin, manager)
    await manager.stop(binding)
    await manager.resume_by_id(binding.codex_thread_id, team_id="T1", channel_id="C_HOME", user_id="U_ME")
    assert not manager.store.by_codex(binding.codex_thread_id).sync_paused
    assert manager.store.by_codex(binding.codex_thread_id).thread_ts == binding.thread_ts
    assert all(m.get("thread_ts") for m in manager.gateway.adapter.client.messages)


def test_pause_migrates_existing_binding_database(plugin, tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bindings (team_id TEXT, channel_id TEXT, thread_ts TEXT, user_id TEXT, "
                     "codex_thread_id TEXT UNIQUE, cwd TEXT, created_at REAL, last_used_at REAL)")
        conn.execute("INSERT INTO bindings VALUES ('T','C','1','U','session','/tmp',1,1)")
    store = plugin.BindingStore(path)
    binding = store.by_codex("session")
    assert not binding.sync_paused
    store.pause_sync(binding, True)
    reopened = plugin.BindingStore(path)
    assert reopened.by_codex("session").sync_paused
    assert reopened.in_channel("C") == []
