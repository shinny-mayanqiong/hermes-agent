"""Behavior tests for the Slack ↔ local Codex session plugin."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agent.transports.codex_app_server_session import (
    CodexThreadPage,
    CodexThreadSummary,
    TurnResult,
)
from gateway.config import Platform


PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "codex-slack"


def _load_plugin():
    module_name = "test_codex_slack_plugin_package"
    for name in [key for key in sys.modules if key == module_name or key.startswith(module_name + ".")]:
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def plugin():
    module = _load_plugin()
    module._test_managers = []
    yield module
    for manager in module._test_managers:
        await manager.shutdown()


class FakeSlackClient:
    def __init__(self):
        self.messages: list[dict] = []
        self.ephemeral: list[dict] = []
        self.updates: list[dict] = []
        self._counter = 100
        self.missing: set[str] = set()

    async def chat_postMessage(self, **kwargs):
        self._counter += 1
        message = {**kwargs, "ts": f"{self._counter}.000"}
        self.messages.append(message)
        return message

    async def chat_postEphemeral(self, **kwargs):
        self._counter += 1
        message = {**kwargs, "message_ts": f"{self._counter}.000"}
        self.ephemeral.append(message)
        return message

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}

    async def chat_getPermalink(self, *, channel, message_ts):
        del channel
        if message_ts in self.missing:
            raise RuntimeError("message_not_found")
        return {"ok": True, "permalink": f"https://slack.test/thread/{message_ts}"}


class FakeSlackAdapter:
    def __init__(self, *, authorized=True):
        self.client = FakeSlackClient()
        self.authorized = authorized
        self._bot_message_ts = set()
        self.sent: list[dict] = []
        self.pop_count = 0

    def _get_client(self, channel_id):
        del channel_id
        return self.client

    def _is_interactive_user_authorized(self, user_id, **kwargs):
        del user_id, kwargs
        return self.authorized

    def _pop_slash_context(self, channel_id):
        del channel_id
        self.pop_count += 1
        return None

    def format_message(self, text):
        return text

    async def send(self, channel_id, text, metadata=None):
        self.sent.append({"channel": channel_id, "text": text, "metadata": metadata})
        return SimpleNamespace(success=True)


class FakeGatewayConfig:
    def __init__(self, home="C_HOME"):
        self.home = home

    def get_home_channel(self, platform):
        assert platform == Platform.SLACK
        return SimpleNamespace(chat_id=self.home) if self.home else None


class FakeGateway:
    def __init__(self, adapter=None, *, home="C_HOME", authorized=True):
        self.adapter = adapter or FakeSlackAdapter(authorized=authorized)
        self.adapters = {Platform.SLACK: self.adapter}
        self.config = FakeGatewayConfig(home)
        self.authorized = authorized

    def _is_user_authorized(self, source):
        del source
        return self.authorized


class FakeCatalog:
    def __init__(self, threads=None):
        self.threads = list(threads or [])
        self.list_calls = []
        self.activity = {"status": {"type": "idle"}, "turns": []}
        self.interrupts = []
        self.archived_ids = set()
        self.mutations = []

    def read_activity(self, thread_id, *, after_turn_id=""):
        del thread_id, after_turn_id
        import copy
        return copy.deepcopy(self.activity)

    def interrupt_turn(self, thread_id, turn_id):
        self.interrupts.append((thread_id, turn_id))

    def list_threads(self, **kwargs):
        self.list_calls.append(kwargs)
        threads = [thread for thread in self.threads
                   if (thread.thread_id in self.archived_ids) == kwargs.get("archived", False)]
        start = int(kwargs.get("cursor") or 0)
        end = start + kwargs.get("limit", 20)
        return CodexThreadPage(threads[start:end], next_cursor=str(end) if end < len(threads) else None)

    def archive_thread(self, thread_id):
        self.mutations.append(("archive", thread_id))
        self.archived_ids.add(thread_id)

    def unarchive_thread(self, thread_id):
        self.mutations.append(("unarchive", thread_id))
        self.archived_ids.discard(thread_id)

    def delete_thread(self, thread_id):
        self.mutations.append(("delete", thread_id))
        self.threads = [thread for thread in self.threads if thread.thread_id != thread_id]

    def read_thread(self, thread_id):
        for thread in self.threads:
            if thread.thread_id == thread_id:
                return thread
        raise RuntimeError("not found")


class FakeSession:
    instances = []
    next_thread_id = "codex-new-1"

    def __init__(self, *, cwd, resume_thread_id=None, approval_callback=None, on_event=None, **kwargs):
        del kwargs
        self.cwd = cwd
        self.resume_thread_id = resume_thread_id
        self.approval_callback = approval_callback
        self.on_event = on_event
        self.closed = False
        self.interrupted = False
        FakeSession.instances.append(self)

    def ensure_started(self):
        return self.resume_thread_id or self.next_thread_id

    def run_turn(self, text):
        if self.on_event:
            self.on_event({"method": "item/started", "params": {"item": {"type": "commandExecution"}}})
        return TurnResult(final_text=f"answer: {text}", thread_id=self.ensure_started(), turn_id="turn-slack")

    def request_interrupt(self):
        self.interrupted = True

    def close(self):
        self.closed = True


def _summary(thread_id="codex-1", cwd="/tmp/project", title="Session"):
    return CodexThreadSummary(
        thread_id=thread_id,
        title=title,
        cwd=str(cwd),
        source_kind="cli",
        created_at=10,
        updated_at=20,
        recency_at=30,
    )


def _event(text, *, channel="C_HOME", thread_id="", user_id="U_ME", team="T1"):
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(
            platform=Platform.SLACK,
            chat_id=channel,
            thread_id=thread_id or None,
            user_id=user_id,
            user_name="Me",
            guild_id=None,
        ),
        raw_message={"team_id": team, "text": text},
    )


def _hermes_codex_event(args, **kwargs):
    event = _event(f"codex {args}".strip(), **kwargs)
    event.raw_message["command"] = "/hermes"
    return event


def _manager(plugin, tmp_path, *, threads=None, allowed_root=None):
    root = Path(allowed_root or tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    default = root / "project"
    default.mkdir(exist_ok=True)
    config = plugin.PluginConfig(
        default_cwd=str(default),
        allowed_roots=(str(root),),
        page_size=20,
        queue_limit=5,
        max_live_sessions=5,
        idle_timeout_seconds=1800,
        approval_timeout_seconds=2,
    )
    store = plugin.BindingStore(tmp_path / "codex-slack.sqlite3")
    manager = plugin.CodexSlackManager(
        config=config,
        store=store,
        catalog=FakeCatalog(threads),
        session_factory=FakeSession,
    )
    manager.gateway = FakeGateway()
    plugin._test_managers.append(manager)
    return manager


def _binding(plugin, manager, *, thread_ts="101.000", codex_id="codex-1"):
    binding = plugin.Binding(
        team_id="T1",
        channel_id="C_HOME",
        thread_ts=thread_ts,
        user_id="U_ME",
        codex_thread_id=codex_id,
        cwd=manager.config.default_cwd,
        created_at=time.time(),
        last_used_at=time.time(),
    )
    manager.store.create(binding)
    return binding


def test_binding_store_persists_and_enforces_bijection(plugin, tmp_path):
    store = plugin.BindingStore(tmp_path / "state" / "codex-slack.sqlite3")
    binding = plugin.Binding("T", "C", "1.0", "U", "codex-1", "/tmp", 1.0, 1.0)
    store.create(binding)

    reopened = plugin.BindingStore(store.path)
    assert reopened.by_slack("T", "C", "1.0") == binding
    assert reopened.by_codex("codex-1") == binding
    with pytest.raises(plugin.BindingConflict):
        reopened.create(plugin.Binding("T", "C", "2.0", "U", "codex-1", "/tmp", 2.0, 2.0))


def test_shared_daemon_wires_catalog_and_sessions_to_unix_socket(plugin, tmp_path):
    config = plugin.PluginConfig(
        default_cwd=str(tmp_path),
        allowed_roots=(str(tmp_path),),
        shared_daemon=True,
        daemon_socket="/tmp/codex-shared.sock",
    )
    manager = plugin.CodexSlackManager(
        config=config,
        store=plugin.BindingStore(tmp_path / "bindings.sqlite3"),
    )

    catalog_factory = manager.catalog._client_factory
    assert catalog_factory.keywords["unix_socket"] == "/tmp/codex-shared.sock"
    assert manager.session_factory.keywords["client_factory"] is catalog_factory
    assert manager.session_factory.keywords["allow_active_turn_steer"] is True


def test_resolve_cwd_blocks_parent_and_symlink_escape(plugin, tmp_path):
    allowed = tmp_path / "repos"
    allowed.mkdir()
    nested = allowed / "team" / "project" / "worktree"
    nested.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "link").symlink_to(outside, target_is_directory=True)
    manager = _manager(plugin, tmp_path / "state", allowed_root=allowed)

    assert manager.resolve_cwd("") == str((allowed / "project").resolve())
    assert manager.resolve_cwd("team/project/worktree") == str(nested.resolve())
    assert manager.resolve_cwd(str(nested)) == str(nested.resolve())
    with pytest.raises(ValueError, match="允许范围"):
        manager.resolve_cwd(str(outside))
    with pytest.raises(ValueError, match="允许范围"):
        manager.resolve_cwd(str(allowed / "link"))


def test_pre_dispatch_is_home_channel_scoped_without_user_filter(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    gateway = manager.gateway

    assert manager.pre_dispatch(_event("hello"), gateway)["action"] == "skip"
    assert manager.pre_dispatch(_event("hello", channel="C_OTHER"), gateway) is None
    assert manager.pre_dispatch(_event("/help"), gateway) is None

    unauthorized = FakeGateway(authorized=False)
    assert manager.pre_dispatch(_event("/codex sessions"), unauthorized)["action"] == "skip"


@pytest.mark.asyncio
async def test_existing_hermes_slash_routes_to_codex_without_native_manifest(plugin, tmp_path):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    event = _hermes_codex_event("sessions")

    assert manager.pre_dispatch(event, manager.gateway)["action"] == "skip"
    await asyncio.gather(*manager._handlers)

    assert "最近的 Codex sessions" in manager.gateway.adapter.client.ephemeral[-1]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["/odoo-hedge-codex", "!odoo-hedge-codex", "hermes"])
async def test_project_entry_opens_cards_without_arguments(plugin, tmp_path, entry):
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=tmp_path / "project")])
    event = _event(entry)
    if entry == "hermes":
        event.text = "odoo-hedge-codex"
        event.raw_message = {"command": "/hermes", "text": "odoo-hedge-codex", "team_id": "T1"}
    assert manager.pre_dispatch(event, manager.gateway)["action"] == "skip"
    await asyncio.gather(*manager._handlers)
    message = manager.gateway.adapter.client.ephemeral[-1]
    assert "最近的 Codex sessions" in message["text"]
    assert any(block.get("expand") for block in message["blocks"])


@pytest.mark.parametrize("text,expected", [
    ("/odoo-hedge-codex new", "new"),
    ("/odoo-hedge-codex sessions archived", "sessions archived"),
    ("/odoo-hedge-codex resume thread-123 /tmp/project", "resume thread-123 /tmp/project"),
    ("/odoo-hedge-codex help", "help"),
    ("/odoo-hedge-codex-extra", None),
])
def test_project_command_reuses_subcommands_with_exact_name_matching(plugin, text, expected):
    assert plugin._command_args(text) == expected


@pytest.mark.asyncio
async def test_new_creates_codex_then_root_and_persists_binding(plugin, tmp_path):
    FakeSession.instances.clear()
    manager = _manager(plugin, tmp_path)

    await manager.handle_command(_event("/codex new"))

    binding = manager.store.by_codex("codex-new-1")
    assert binding is not None
    assert binding.cwd == manager.config.default_cwd
    assert manager.gateway.adapter.client.messages[0]["channel"] == "C_HOME"
    assert "Codex session 已创建" in manager.gateway.adapter.client.messages[0]["text"]
    assert FakeSession.instances[0].closed is False
    await manager.enqueue_turn(binding, "first prompt", "U_ME")
    await manager._runtimes[binding.codex_thread_id].worker
    assert len(FakeSession.instances) == 1
    assert manager.gateway.adapter.sent[-1]["text"] == "answer: first prompt"
    assert manager.gateway.adapter.pop_count >= 1


@pytest.mark.asyncio
async def test_resume_reuses_existing_binding_instead_of_duplicating(plugin, tmp_path):
    thread = _summary(cwd=tmp_path / "project")
    manager = _manager(plugin, tmp_path, threads=[thread])
    binding = _binding(plugin, manager)

    await manager.resume_by_id("codex-1", team_id="T1", channel_id="C_HOME", user_id="U_ME")

    persisted = manager.store.by_codex("codex-1")
    assert persisted is not None
    assert persisted.codex_thread_id == binding.codex_thread_id
    assert persisted.thread_ts == binding.thread_ts
    assert not manager.gateway.adapter.client.messages
    assert "打开原 thread" in manager.gateway.adapter.client.ephemeral[-1]["text"]


@pytest.mark.asyncio
async def test_resume_can_replace_a_missing_original_cwd(plugin, tmp_path):
    missing = tmp_path / "deleted-worktree"
    replacement = tmp_path / "team" / "replacement"
    replacement.mkdir(parents=True)
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=missing)])

    await manager.handle_command(
        _event("/codex resume codex-1 team/replacement")
    )

    binding = manager.store.by_codex("codex-1")
    assert binding is not None
    assert binding.cwd == str(replacement.resolve())
    assert FakeSession.instances[-1].cwd == str(replacement.resolve())


@pytest.mark.asyncio
async def test_sessions_page_is_ephemeral_has_twenty_limit_and_buttons(plugin, tmp_path):
    threads = [_summary(f"codex-{idx:04}", tmp_path / "project", f"Session {idx}") for idx in range(21)]
    manager = _manager(plugin, tmp_path, threads=threads)
    _binding(plugin, manager, codex_id="codex-0000")

    await manager.show_sessions("C_HOME", "U_ME")

    assert manager.catalog.list_calls[0] == {"cursor": None, "limit": 20, "archived": False}
    blocks = manager.gateway.adapter.client.ephemeral[-1]["blocks"]
    cards = [block for block in blocks if block["type"] == "section" and block.get("expand")]
    assert len(cards) == 20
    assert cards[0]["text"]["text"].startswith("0000 · Session 0\n")
    assert "创建：1970-01-01 00:00:10" in cards[0]["text"]["text"]
    assert "更新：1970-01-01 00:00:20" in cards[0]["text"]["text"]  # updatedAt, not recencyAt
    actions = [action for block in blocks[:-1] if block["type"] == "actions" for action in block["elements"]]
    assert len({action["action_id"] for action in actions}) == len(actions)
    assert [action["text"]["text"] for action in actions[:3]] == ["Resume", "Archive", "Delete"]
    assert "不可恢复" in actions[2]["confirm"]["text"]["text"]
    assert any(button["text"]["text"] == "Next" for button in blocks[-1]["elements"])


@pytest.mark.asyncio
async def test_sessions_page_distinguishes_a_deleted_cwd_from_disallowed(plugin, tmp_path):
    missing = tmp_path / "deleted-worktree"
    manager = _manager(plugin, tmp_path, threads=[_summary(cwd=missing)])

    await manager.show_sessions("C_HOME", "U_ME")

    blocks = manager.gateway.adapter.client.ephemeral[-1]["blocks"]
    card = next(block for block in blocks if block["type"] == "section" and block.get("expand"))
    assert str(missing) in card["text"]["text"]
    resume = next(block for block in blocks if block["type"] == "actions")["elements"][0]
    await manager.handle_action(resume["action_id"], {"channel": {"id": "C_HOME"}, "user": {"id": "U_ME"}}, resume)
    assert "目录不存在" in manager.gateway.adapter.client.ephemeral[-1]["text"]


@pytest.mark.asyncio
async def test_bound_thread_turn_then_stop_releases_process_and_preserves_binding(plugin, tmp_path):
    FakeSession.instances.clear()
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)

    await manager.enqueue_turn(binding, "continue", "U_ME")
    runtime = manager._runtimes[binding.codex_thread_id]
    await runtime.worker

    assert manager.gateway.adapter.sent[-1]["text"] == "answer: continue"
    assert FakeSession.instances[-1].resume_thread_id == "codex-1"
    assert FakeSession.instances[-1].closed is False

    await manager.stop(binding)

    assert FakeSession.instances[-1].closed is True
    persisted = manager.store.by_codex("codex-1")
    assert persisted is not None
    assert persisted.codex_thread_id == binding.codex_thread_id
    assert persisted.thread_ts == binding.thread_ts
    assert binding.codex_thread_id not in manager._runtimes
    assert "已停止并释放资源" in manager.gateway.adapter.client.messages[-1]["text"]
    if manager._reaper:
        manager._reaper.cancel()


@pytest.mark.asyncio
async def test_bound_thread_accepts_messages_from_any_user_in_home_channel(plugin, tmp_path):
    FakeSession.instances.clear()
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)

    await manager.handle_event(
        _event("continue", thread_id=binding.thread_ts, user_id="U_OTHER")
    )
    runtime = manager._runtimes[binding.codex_thread_id]
    await runtime.worker

    assert manager.gateway.adapter.sent[-1]["text"] == "answer: continue"
    assert not manager.gateway.adapter.client.ephemeral
    if manager._reaper:
        manager._reaper.cancel()


@pytest.mark.asyncio
async def test_same_session_queue_rejects_sixth_waiting_message(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    runtime = manager._runtime(binding)
    runtime.active = True
    for idx in range(5):
        await manager.enqueue_turn(binding, f"queued-{idx}", "U_ME")

    await manager.enqueue_turn(binding, "overflow", "U_ME")

    assert len(runtime.queue) == 5
    assert "队列已满" in manager.gateway.adapter.client.messages[-1]["text"]
    if manager._reaper:
        manager._reaper.cancel()


@pytest.mark.asyncio
async def test_lru_keeps_capacity_but_evicts_to_reserve_new_slot(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    manager.config = plugin.PluginConfig(
        default_cwd=manager.config.default_cwd,
        allowed_roots=manager.config.allowed_roots,
        max_live_sessions=1,
    )
    binding = _binding(plugin, manager)
    runtime = manager._runtime(binding)
    runtime.session = FakeSession(cwd=binding.cwd, resume_thread_id=binding.codex_thread_id)

    await manager._prune_runtimes()
    assert binding.codex_thread_id in manager._runtimes
    assert runtime.session is not None

    session = runtime.session
    await manager._prune_runtimes(reserve_slot=True)
    assert binding.codex_thread_id not in manager._runtimes
    assert session.closed is True


@pytest.mark.asyncio
async def test_stop_denies_pending_approval_before_closing(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    runtime = manager._runtime(binding)
    runtime.session = FakeSession(cwd=binding.cwd, resume_thread_id=binding.codex_thread_id)
    pending = plugin._PendingApproval(binding, "command", "description")
    manager._approvals["approval-token"] = pending

    await manager.stop(binding)

    assert pending.event.is_set()
    assert pending.decision == "deny"
    assert runtime.session is None


@pytest.mark.asyncio
async def test_slack_approval_button_unblocks_worker_callback(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    callback = manager._approval_callback(binding, asyncio.get_running_loop())

    task = asyncio.create_task(asyncio.to_thread(callback, "git status", "run command"))
    for _ in range(100):
        if manager._approvals:
            break
        await asyncio.sleep(0.01)
    token = next(iter(manager._approvals))
    await manager.handle_approval_action(
        plugin.ACTION_APPROVE_ONCE,
        {"channel": {"id": "C_HOME"}, "user": {"id": "U_OTHER"}, "team": {"id": "T1"}},
        {"value": token},
    )

    assert await task == "once"


def test_register_exposes_command_hook_and_namespaced_actions(plugin):
    ctx = SimpleNamespace(
        commands=[],
        hooks=[],
        actions=[],
        register_command=lambda *args, **kwargs: ctx.commands.append((args, kwargs)),
        register_hook=lambda *args: ctx.hooks.append(args),
        register_slack_action_handler=lambda *args: ctx.actions.append(args),
    )

    plugin.register(ctx)

    registered = {args[0]: metadata for args, metadata in ctx.commands}
    assert registered["odoo-hedge-codex"]["platforms"] == ("slack",)
    assert "codex" in registered
    assert dict(ctx.hooks)["pre_gateway_dispatch"] == plugin._pre_gateway_dispatch
    assert dict(ctx.hooks)["on_gateway_start"] == plugin._on_gateway_start
    assert dict(ctx.hooks)["on_gateway_stop"] == plugin._on_gateway_stop
    for action_id in (
        plugin.ACTION_PAGE,
        plugin.ACTION_RESUME,
        plugin.ACTION_OPEN,
        plugin.ACTION_ARCHIVE,
        plugin.ACTION_DELETE,
        plugin.ACTION_APPROVE_ONCE,
        plugin.ACTION_APPROVE_SESSION,
        plugin.ACTION_DENY,
    ):
        matches = [callback for pattern, callback in ctx.actions
                   if (pattern.fullmatch(action_id) if hasattr(pattern, "fullmatch") else pattern == action_id)]
        assert len(matches) == 1
    for action_id in (plugin.ACTION_PAGE, plugin.ACTION_RESUME, plugin.ACTION_ARCHIVE, plugin.ACTION_DELETE):
        assert sum(bool(pattern.fullmatch(action_id + ":row-token")) for pattern, _ in ctx.actions
                   if hasattr(pattern, "fullmatch")) == 1


def test_active_writer_error_is_user_friendly(plugin):
    message = plugin._friendly_codex_error(
        RuntimeError("thread abc already has an active writer")
    )

    assert "另一个本机 Codex 客户端" in message
    assert "active writer" not in message


@pytest.mark.asyncio
async def test_bound_thread_controls_ignore_injected_slack_history(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    event = _event("!status", thread_id=binding.thread_ts)
    event.text = "[Thread context — prior messages:]\nold request\n!status"
    await manager.handle_event(event)
    assert "状态：" in manager.gateway.adapter.client.messages[-1]["text"]
    assert not manager._runtimes


@pytest.mark.asyncio
async def test_bot_mention_and_cached_workspace_route_controls(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    manager.gateway.adapter._bot_user_id = "U_BOT"
    manager.gateway.adapter._channel_team = {"C_HOME": "T1"}
    event = _event("<@U_BOT> !status", thread_id=binding.thread_ts, team="")
    await manager.handle_event(event)
    assert "状态：" in manager.gateway.adapter.client.messages[-1]["text"]
    assert manager._event_text(_event("<@U_OTHER> please review")) == "<@U_OTHER> please review"


@pytest.mark.asyncio
async def test_simultaneous_replies_share_one_worker_and_preserve_order(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    await manager.enqueue_turn(binding, "first", "U_ME")
    worker = manager._runtimes[binding.codex_thread_id].worker
    await manager.enqueue_turn(binding, "second", "U_ME")
    assert manager._runtimes[binding.codex_thread_id].worker is worker
    await worker
    assert [message["text"] for message in manager.gateway.adapter.sent] == [
        "answer: first", "answer: second",
    ]


def _activity(turn_id, text, status="inProgress"):
    return {
        "status": {"type": "active" if status == "inProgress" else "idle"},
        "turns": [{"id": turn_id, "status": status, "completedAt": int(time.time()) + 1,
                   "items": [{"type": "agentMessage", "id": "item-1", "text": text}]}],
    }


@pytest.mark.asyncio
async def test_terminal_progress_and_completion_survive_manager_restart(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("terminal-1", "正在检查调用路径")
    await manager._sync_binding(binding)
    assert "正在检查调用路径" in manager.gateway.adapter.client.messages[-1]["text"]
    progress_ts = manager.gateway.adapter.client.messages[-1]["ts"]
    await manager._sync_binding(binding)
    assert len(manager.gateway.adapter.client.messages) == 1

    restarted = _manager(plugin, tmp_path)
    restarted.gateway = manager.gateway
    restarted.catalog.activity = _activity("terminal-1", "修复已完成，验证通过", "completed")
    await restarted._sync_binding(binding)
    assert restarted.gateway.adapter.client.updates[-1]["ts"] == progress_ts
    assert restarted.gateway.adapter.sent[-1]["text"] == "修复已完成，验证通过"
    await restarted._sync_binding(binding)
    assert len(restarted.gateway.adapter.sent) == 1
    assert not restarted._runtimes  # Observing never resumes or starts a model turn.


@pytest.mark.asyncio
async def test_sync_retries_failed_slack_delivery_before_marking_done(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("terminal-2", "result", "completed")
    sender = manager.gateway.adapter.send

    async def fail(*args, **kwargs):
        return SimpleNamespace(success=False, error="ratelimited")

    manager.gateway.adapter.send = fail
    with pytest.raises(RuntimeError, match="ratelimited"):
        await manager._sync_binding(binding)
    assert not manager.store.progress(binding.codex_thread_id, "terminal-2")["completed"]
    manager.gateway.adapter.send = sender
    await manager._sync_binding(binding)
    assert manager.gateway.adapter.sent[-1]["text"] == "result"
    assert len(manager.gateway.adapter.client.messages) == 1


@pytest.mark.asyncio
async def test_status_and_stop_use_terminal_owned_turn_without_local_worker(plugin, tmp_path):
    from dataclasses import replace
    manager = _manager(plugin, tmp_path)
    manager.config = replace(manager.config, shared_daemon=True)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("terminal-active", "正在运行测试")
    await manager.status(binding)
    assert "运行中" in manager.gateway.adapter.client.messages[-1]["text"]
    assert "正在运行测试" in manager.gateway.adapter.client.messages[-1]["text"]
    await manager.stop(binding)
    assert manager.catalog.interrupts == [(binding.codex_thread_id, "terminal-active")]


@pytest.mark.asyncio
async def test_sync_seeds_history_and_skips_slack_owned_turns(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)
    manager.catalog.activity = _activity("old-turn", "old answer", "completed")
    await manager._sync_binding(binding, initialize=True)
    assert not manager.gateway.adapter.sent
    runtime = manager._runtime(binding)
    runtime.active = True
    manager.catalog.activity = _activity("slack-turn", "in flight")
    await manager._sync_binding(binding)
    assert not manager.gateway.adapter.client.messages
    runtime.active = False


@pytest.mark.asyncio
async def test_interrupted_turn_is_not_reported_as_completed(plugin, tmp_path):
    manager = _manager(plugin, tmp_path)
    binding = _binding(plugin, manager)

    class InterruptedSession(FakeSession):
        def run_turn(self, text):
            return TurnResult(interrupted=True, thread_id=binding.codex_thread_id, turn_id="interrupted")

    manager.session_factory = InterruptedSession
    await manager.enqueue_turn(binding, "work", "U_ME")
    await manager._runtimes[binding.codex_thread_id].worker
    assert "已中断" in manager.gateway.adapter.client.updates[-1]["text"]
    assert not manager.gateway.adapter.sent
