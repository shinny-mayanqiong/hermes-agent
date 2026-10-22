"""Session browser behavior: scoped paging and explicit lifecycle actions."""

import asyncio
import sqlite3
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from tests.plugins.test_codex_slack_plugin import (
    FakeSession, _binding, _event, _manager, _summary, plugin,
)


BODY = {"channel": {"id": "C_HOME"}, "user": {"id": "U_ME"}, "team": {"id": "T1"}}


def cards(manager):
    result = []
    sections = []
    for block in manager.gateway.adapter.client.ephemeral[-1]["blocks"]:
        if block["type"] == "section" and block.get("expand"):
            sections.append(block["text"]["text"])
        elif block["type"] == "actions" and sections:
            result.append({"text": "".join(sections), "actions": block["elements"]})
            sections = []
    return result


def button(manager, label, row=1):
    return next(action for action in cards(manager)[row - 1]["actions"]
                if action["text"]["text"] == label)


async def click(manager, element):
    await manager.handle_action(element["action_id"], BODY, element)


@pytest.mark.asyncio
async def test_filter_before_paging_includes_removed_worktrees_excludes_prefixes_and_symlinks(plugin, tmp_path):
    repo, worktrees = tmp_path / "project", tmp_path / "worktrees"
    worktrees.mkdir()
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, allowed_roots=(str(repo), str(worktrees)))
    outside = tmp_path / "other"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    # Two disallowed entries per allowed entry force scans across upstream pages.
    allowed = [_summary(f"project-{i:04}", worktrees / f"removed-{i}") for i in range(25)]
    for i, item in enumerate(allowed):
        manager.catalog.threads.extend([_summary(f"other-{i}", outside), item,
                                       _summary(f"lookalike-{i}", tmp_path / "project-copy")])
    manager.catalog.threads.insert(0, _summary("symlink", repo / "escape"))
    manager.catalog.threads.append(_summary("empty-cwd", ""))
    await manager.show_sessions("C_HOME", "U_ME")
    assert [card["text"].partition(" · ")[0] for card in cards(manager)] == [item.thread_id[-4:] for item in allowed[:20]]
    first = [card["text"] for card in cards(manager)]
    navigation = manager.gateway.adapter.client.ephemeral[-1]["blocks"][-1]["elements"]
    await click(manager, next(item for item in navigation if item["text"]["text"] == "Next"))
    assert [card["text"].partition(" · ")[0] for card in cards(manager)] == [item.thread_id[-4:] for item in allowed[20:]]
    navigation = manager.gateway.adapter.client.ephemeral[-1]["blocks"][-1]["elements"]
    await click(manager, next(item for item in navigation if item["text"]["text"] == "Previous"))
    assert [card["text"] for card in cards(manager)] == first


@pytest.mark.asyncio
async def test_archive_resume_delete_keep_binding_and_checkpoint_consistent(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    binding = _binding(plugin, manager)
    manager.store.save_progress(binding.codex_thread_id, "delivered", completed=True)
    runtime = manager._runtime(binding)
    runtime.session = FakeSession(cwd=binding.cwd)
    session = runtime.session
    await manager.show_sessions("C_HOME", "U_ME")
    stale_delete = button(manager, "Delete")
    await click(manager, button(manager, "Archive"))
    assert manager.catalog.mutations == [("archive", "codex-1")]
    assert session.closed
    assert manager.store.by_codex("codex-1").state == "archived"
    assert manager.store.in_channel("C_HOME") == []
    assert manager.store.progress("codex-1", "delivered")["completed"]
    await click(manager, stale_delete)
    assert "过期" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await manager.handle_event(_event("continue", thread_id=binding.thread_ts))
    assert "已归档" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await manager._sync_binding(binding)
    assert not manager.gateway.adapter.client.messages

    # Reopen the real SQLite store as after a process restart.
    manager.store = plugin.BindingStore(manager.store.path)
    await manager.handle_command(_event("/codex sessions archived"))
    assert "已归档" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await click(manager, button(manager, "Resume"))
    assert manager.catalog.mutations[-1] == ("unarchive", "codex-1")
    restored = manager.store.by_codex("codex-1")
    assert restored.state == "active" and restored.thread_ts == binding.thread_ts
    assert "打开原 thread" in manager.gateway.adapter.client.ephemeral[-1]["text"]

    await manager.show_sessions("C_HOME", "U_ME")
    delete = button(manager, "Delete")
    await click(manager, delete)
    assert manager.catalog.mutations[-1] == ("delete", "codex-1")
    assert manager.store.by_codex("codex-1").state == "deleted"
    assert manager.store.progress("codex-1") is None
    await manager.handle_event(_event("continue", thread_id=binding.thread_ts))
    assert "永久删除" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await click(manager, delete)
    assert manager.catalog.mutations.count(("delete", "codex-1")) == 1


@pytest.mark.asyncio
async def test_no_next_button_when_only_unrelated_sessions_remain(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, allowed_roots=(manager.config.default_cwd,))
    manager.catalog.threads = [_summary(f"match-{i:04}", tmp_path / "project") for i in range(20)]
    manager.catalog.threads.extend(_summary(f"other-{i}", tmp_path / "other") for i in range(23))
    await manager.show_sessions("C_HOME", "U_ME")
    assert len(cards(manager)) == 20
    navigation = manager.gateway.adapter.client.ephemeral[-1]["blocks"][-1]["elements"]
    assert all(action["text"]["text"] != "Next" for action in navigation)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["Archive", "Delete"])
@pytest.mark.parametrize("busy", ["terminal", "slack", "out_of_scope", "rpc_failure"])
async def test_mutations_recheck_scope_and_activity_preserve_state_on_failure(plugin, tmp_path, kind, busy):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    manager.config = replace(manager.config, allowed_roots=(manager.config.default_cwd,))
    binding = _binding(plugin, manager)
    await manager.show_sessions("C_HOME", "U_ME")
    action = button(manager, kind)
    if busy == "terminal":
        manager.catalog.activity = {"status": {"type": "active"}, "turns": []}
    elif busy == "slack":
        manager._runtime(binding).active = True
    elif busy == "out_of_scope":
        manager.catalog.threads = [_summary(cwd=tmp_path / "other")]
    else:
        def fail(_):
            raise RuntimeError("daemon failure")
        manager.catalog.archive_thread = manager.catalog.delete_thread = fail
    await click(manager, action)
    assert manager.catalog.mutations == []
    assert manager.store.by_codex("codex-1").state == "active"
    assert "操作未完成" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    assert not manager._managing_sessions


@pytest.mark.asyncio
async def test_action_kind_channel_and_full_id_do_not_depend_on_short_id(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[
        _summary("first-abcd", tmp_path / "project"), _summary("second-abcd", tmp_path / "project")])
    await manager.show_sessions("C_HOME", "U_ME")
    first, second = button(manager, "Delete", 1), button(manager, "Delete", 2)
    assert first["value"] != second["value"]
    await manager.handle_action(first["action_id"], {**BODY, "channel": {"id": "OTHER"}}, first)
    assert manager.catalog.mutations == []
    await manager.handle_action(plugin.ACTION_RESUME, BODY, first)
    assert manager.catalog.mutations == []
    await click(manager, second)
    assert manager.catalog.mutations == [("delete", "second-abcd")]


@pytest.mark.asyncio
async def test_scope_cannot_be_bypassed_with_resume_directory_override(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "other")])
    manager.config = replace(manager.config, allowed_roots=(manager.config.default_cwd,))
    await manager.resume_by_id("codex-1", team_id="T1", channel_id="C_HOME", user_id="U_ME",
                               cwd_override=manager.config.default_cwd)
    assert "项目路径范围" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    assert manager.store.by_codex("codex-1") is None


@pytest.mark.asyncio
async def test_archive_serializes_against_mirror_and_blocks_new_prompt(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    binding = _binding(plugin, manager)
    lock = manager._mirror_locks.setdefault("codex-1", asyncio.Lock())
    await lock.acquire()
    task = asyncio.create_task(manager.manage_session("archive", "codex-1", channel_id="C_HOME", user_id="U_ME"))
    try:
        await asyncio.sleep(0)
        await manager.enqueue_turn(binding, "new prompt", "U_ME")
        assert not manager._runtimes
        assert not manager.catalog.mutations
    finally:
        lock.release()
    await task
    manager._sync_binding_locked = AsyncMock(wraps=manager._sync_binding_locked)
    await manager._sync_binding(binding)  # stale binding from a pre-archive scan
    assert manager.store.in_channel("C_HOME") == []


@pytest.mark.asyncio
async def test_cards_preserve_full_paths_times_and_keep_buttons_outside_content(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    path = str(tmp_path / ("long worktree " * 90))
    manager.catalog.threads = [_summary(f"thread-{i:04}", path, "<&>" * 100) for i in range(20)]
    await manager.show_sessions("C_HOME", "U_ME")
    blocks = manager.gateway.adapter.client.ephemeral[-1]["blocks"]
    assert len(blocks) <= 50
    assert all(block["type"] not in {"table", "data_table"} for block in blocks)
    assert len(cards(manager)) == 20
    for card in cards(manager):
        assert path in card["text"]
        assert "创建：1970-01-01 00:00:10" in card["text"]
        assert "更新：1970-01-01 00:00:20" in card["text"]
        assert "状态" not in card["text"]
        assert [action["text"]["text"] for action in card["actions"]] == ["Resume", "Archive", "Delete"]
    for block in blocks:
        if block["type"] == "section" and block.get("expand"):
            assert block["text"]["type"] == "plain_text"
            assert len(block["text"]["text"]) <= 3000


@pytest.mark.asyncio
async def test_very_long_paths_split_without_losing_data_or_separating_buttons(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    # A valid maximum-length path can exceed a Slack section's character limit.
    path = str(tmp_path) + "/branch" * 560
    manager.catalog.threads = [_summary(f"thread-{i:04}", path) for i in range(20)]
    await manager.show_sessions("C_HOME", "U_ME")
    count = 0
    for message in manager.gateway.adapter.client.ephemeral:
        assert len(message["blocks"]) <= 50
        text = ""
        for block in message["blocks"]:
            if block["type"] == "section" and block.get("expand"):
                assert len(block["text"]["text"]) <= 3000
                text += block["text"]["text"]
            elif block["type"] == "actions" and text:
                assert path in text
                assert block["elements"][0]["text"]["text"] == "Resume"
                count += 1
                text = ""
        assert not text  # Buttons follow each complete card in the same message.
    assert count == 20


def test_store_migrates_existing_bindings_without_losing_progress(plugin, tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE bindings (team_id TEXT, channel_id TEXT, thread_ts TEXT,
                     user_id TEXT, codex_thread_id TEXT UNIQUE, cwd TEXT, created_at REAL,
                     last_used_at REAL, PRIMARY KEY(team_id, channel_id, thread_ts))""")
        conn.execute("INSERT INTO bindings VALUES ('T1','C_HOME','1.0','U_ME','codex-1','/tmp',1,2)")
    store = plugin.BindingStore(path)
    binding = store.by_codex("codex-1")
    assert binding.state == "active"
    store.save_progress("codex-1", "done", completed=True)
    store.set_state(binding, "archived")
    reopened = plugin.BindingStore(path)
    assert reopened.by_codex("codex-1").state == "archived"
    assert reopened.progress("codex-1", "done")["completed"]
