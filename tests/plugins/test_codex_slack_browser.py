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
    return [{"text": block["elements"][0]["text"]["text"], "actions": block["elements"]}
            for block in manager.gateway.adapter.client.ephemeral[-1]["blocks"]
            if block["type"] == "actions" and block["elements"][0]["action_id"].startswith("codex_slack_resume:")]


def button(manager, label, row=1):
    return next(action for action in cards(manager)[row - 1]["actions"]
                if action["action_id"].startswith(f"codex_slack_{label.lower()}:"))


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
    assert [manager._actions[card["actions"][0]["value"]].payload["codex_thread_id"]
            for card in cards(manager)] == [item.thread_id for item in allowed[:20]]
    first = [card["text"] for card in cards(manager)]
    navigation = manager.gateway.adapter.client.ephemeral[-1]["blocks"][-1]["elements"]
    await click(manager, next(item for item in navigation if item["text"]["text"] == "Next"))
    assert [manager._actions[card["actions"][0]["value"]].payload["codex_thread_id"]
            for card in cards(manager)] == [item.thread_id for item in allowed[20:]]
    navigation = manager.gateway.adapter.client.ephemeral[-1]["blocks"][-1]["elements"]
    await click(manager, next(item for item in navigation if item["text"]["text"] == "Previous"))
    assert [card["text"] for card in cards(manager)] == first


@pytest.mark.asyncio
async def test_archive_resume_keep_binding_and_checkpoint_consistent(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    binding = _binding(plugin, manager)
    manager.store.save_progress(binding.codex_thread_id, "delivered", completed=True)
    runtime = manager._runtime(binding)
    runtime.session = FakeSession(cwd=binding.cwd)
    session = runtime.session
    await manager.show_sessions("C_HOME", "U_ME")
    stale_archive = button(manager, "Archive")
    await click(manager, button(manager, "Archive"))
    assert manager.catalog.mutations == [("archive", "codex-1")]
    assert session.closed
    assert manager.store.by_codex("codex-1").state == "archived"
    assert manager.store.in_channel("C_HOME") == []
    assert manager.store.progress("codex-1", "delivered")["completed"]
    await click(manager, stale_archive)
    assert "过期" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await manager.handle_event(_event("continue", thread_id=binding.thread_ts))
    assert "已归档" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    await manager._sync_binding(binding)
    assert not manager.gateway.adapter.client.messages

    # Reopen the real SQLite store as after a process restart.
    manager.store = plugin.BindingStore(manager.store.path)
    await manager.handle_command(_event("/codex sessions archived"))
    assert "已归档" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    assert [action["action_id"].split(":")[0] for action in cards(manager)[0]["actions"]] == [
        "codex_slack_resume",
    ]
    await click(manager, button(manager, "Resume"))
    assert manager.catalog.mutations[-1] == ("unarchive", "codex-1")
    restored = manager.store.by_codex("codex-1")
    assert restored.state == "active" and restored.thread_ts == binding.thread_ts
    assert "打开原 thread" in manager.gateway.adapter.client.ephemeral[-1]["text"]
    assert manager.store.progress("codex-1", "delivered")["completed"]


@pytest.mark.asyncio
async def test_removed_delete_action_cannot_mutate_a_session(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    binding = _binding(plugin, manager)
    token = manager._action_token("delete", channel_id="C_HOME", payload={"codex_thread_id": "codex-1"})
    await manager.handle_action(f"codex_slack_delete:{token}", BODY, {"value": token})
    assert manager.catalog.mutations == []
    assert manager.store.by_codex(binding.codex_thread_id).state == "active"
    assert "过期" in manager.gateway.adapter.client.ephemeral[-1]["text"]


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
@pytest.mark.parametrize("busy", ["terminal", "slack", "out_of_scope", "rpc_failure"])
async def test_archive_rechecks_scope_and_activity_preserves_state_on_failure(plugin, tmp_path, busy):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    manager.config = replace(manager.config, allowed_roots=(manager.config.default_cwd,))
    binding = _binding(plugin, manager)
    await manager.show_sessions("C_HOME", "U_ME")
    action = button(manager, "Archive")
    if busy == "terminal":
        manager.catalog.activity = {"status": {"type": "active"}, "turns": []}
    elif busy == "slack":
        manager._runtime(binding).active = True
    elif busy == "out_of_scope":
        manager.catalog.threads = [_summary(cwd=tmp_path / "other")]
    else:
        def fail(_):
            raise RuntimeError("daemon failure")
        manager.catalog.archive_thread = fail
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
    first, second = button(manager, "Archive", 1), button(manager, "Archive", 2)
    assert first["value"] != second["value"]
    await manager.handle_action(first["action_id"], {**BODY, "channel": {"id": "OTHER"}}, first)
    assert manager.catalog.mutations == []
    await manager.handle_action(plugin.ACTION_RESUME, BODY, first)
    assert manager.catalog.mutations == []
    await click(manager, second)
    assert manager.catalog.mutations == [("archive", "second-abcd")]


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
    task = asyncio.create_task(manager.archive_session("codex-1", channel_id="C_HOME", user_id="U_ME"))
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
async def test_sessions_use_one_action_row_with_title_and_update(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    path = str(tmp_path / ("long worktree " * 90))
    manager.catalog.threads = [_summary(f"thread-{i:04}", path, "<&>" * 100) for i in range(20)]
    await manager.show_sessions("C_HOME", "U_ME")
    blocks = manager.gateway.adapter.client.ephemeral[-1]["blocks"]
    assert len(blocks) <= 50
    assert all(block["type"] not in {"table", "data_table"} for block in blocks)
    assert len(cards(manager)) == 20
    for card in cards(manager):
        assert path not in card["text"]
        assert "创建" not in card["text"]
        assert "1970-01-01 00:00" in card["text"]
        assert card["text"].startswith("<&>")
        assert "\n" not in card["text"]
        assert "thread-" not in card["text"]
        assert "状态" not in card["text"]
        assert [action["action_id"].split(":")[0] for action in card["actions"]] == [
            "codex_slack_resume", "codex_slack_archive",
        ]
        assert all(action["text"]["type"] == "plain_text" for action in card["actions"])
        assert all(len(action["text"]["text"]) <= 30 for action in card["actions"])
    assert not any(block.get("expand") for block in blocks)


@pytest.mark.asyncio
async def test_long_paths_do_not_expand_cards_or_split_the_page(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    # A valid maximum-length path can exceed a Slack section's character limit.
    path = str(tmp_path) + "/branch" * 560
    manager.catalog.threads = [_summary(f"thread-{i:04}", path) for i in range(20)]
    await manager.show_sessions("C_HOME", "U_ME")
    assert len(manager.gateway.adapter.client.ephemeral) == 1
    blocks = manager.gateway.adapter.client.ephemeral[-1]["blocks"]
    assert len(blocks) <= 50
    assert len(cards(manager)) == 20
    assert all(path not in card["text"] for card in cards(manager))
    assert all("\n" not in card["text"] for card in cards(manager))


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
