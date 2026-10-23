"""Mode controls and durable pause semantics, including delivery races."""

import asyncio
import sqlite3
import threading
from dataclasses import replace

import pytest

from tests.plugins.test_codex_slack_plugin import (
    plugin, _manager, _binding, _event, _activity, _summary,
)


@pytest.mark.asyncio
async def test_stop_is_durable_and_resume_discards_paused_history(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("old", "working")
    await manager._sync_binding(binding)
    await manager.handle_event(_event("!stop", thread_id=binding.thread_ts))
    assert manager.store.by_codex(binding.codex_thread_id).sync_paused
    assert manager.catalog.interrupts == [(binding.codex_thread_id, "old")]
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
    assert "无法确认 Codex 已停止" in manager.gateway.adapter.client.messages[-1]["text"]
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
    assert "先 `!stop`" in manager.gateway.adapter.client.messages[-1]["text"]
    assert runtime.session is None
    runtime.active = False


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
