"""Tests for the Odoo Hedge Server Slack workflow plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "odoo-hedge-server"
    / "__init__.py"
)
SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "odoo-hedge-server"
    / "SKILL.md"
)


def _load_plugin():
    module_name = "test_odoo_hedge_server_plugin"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeSlackClient:
    def __init__(self):
        self.messages = []
        self._counter = 100
        self.response_wrapper = None

    async def chat_postMessage(self, **kwargs):
        self._counter += 1
        ts = f"{self._counter}.000"
        self.messages.append({**kwargs, "ts": ts})
        response = {"ok": True, "ts": ts}
        if self.response_wrapper:
            return self.response_wrapper(response)
        return response


class FakeSlackResponse:
    def __init__(self, data):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakeSlackAdapter:
    def __init__(self):
        self.client = FakeSlackClient()
        self.handled_events = []
        self._bot_message_ts = set()
        self._mentioned_threads = set()
        self._send_slash_ephemeral = AsyncMock()
        self.pop_slash_context_count = 0

    def _get_client(self, chat_id):
        del chat_id
        return self.client

    async def _resolve_user_name(self, user_id, chat_id=""):
        del user_id, chat_id
        return "Alice"

    def _pop_slash_context(self, chat_id):
        del chat_id
        self.pop_slash_context_count += 1
        return {"response_url": "https://slack.example/response"}

    def format_message(self, content):
        return content

    async def handle_message(self, event):
        self.handled_events.append(event)


def _source(*, thread_id=None):
    return SimpleNamespace(
        platform=Platform.SLACK,
        chat_id="C123",
        user_id="U123",
        user_name="",
        thread_id=thread_id,
        guild_id="T123",
        chat_type="group",
        chat_name="general",
    )


def _event(text, *, thread_id=None):
    return SimpleNamespace(
        text=text,
        source=_source(thread_id=thread_id),
        raw_message={"team_id": "T123", "text": text},
    )


def _gateway(adapter, *, authorized=True):
    return SimpleNamespace(
        adapters={Platform.SLACK: adapter},
        _is_user_authorized=lambda source: authorized,
    )


def test_default_config_enables_plugin():
    from hermes_cli.config import DEFAULT_CONFIG

    assert "odoo-hedge-server" in DEFAULT_CONFIG["plugins"]["enabled"]
    server = DEFAULT_CONFIG["mcp_servers"]["odoo-hedge-server"]
    assert server["url"] == "http://192.168.139.7:18079/mcp"
    assert server["timeout"] == 1200


def test_skill_keeps_core_tools_and_documents_operation_map():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    for tool_name in (
        "mcp_odoo_hedge_server_sandbox_healthz",
        "mcp_odoo_hedge_server_ensure_sandbox_version",
        "mcp_odoo_hedge_server_create_sandbox",
        "mcp_odoo_hedge_server_upgrade_sandbox",
        "mcp_odoo_hedge_server_list_sandboxes",
        "mcp_odoo_hedge_server_get_sandbox",
        "mcp_odoo_hedge_server_destroy_sandbox",
        "mcp_odoo_hedge_server_provision_sync_defaults",
    ):
        assert f"`{tool_name}`" in skill

    assert "Forward-Compatible Tool Map" in skill
    assert "`mcp_odoo_hedge_server_list_prompts`" in skill
    assert "`mcp_odoo_hedge_server_get_prompt`" in skill
    assert "`mcp_odoo_hedge_server_list_resources`" in skill
    assert "`mcp_odoo_hedge_server_read_resource`" in skill


def test_register_exposes_slack_command_and_hook_without_tools():
    plugin = _load_plugin()

    class FakeCtx:
        def __init__(self):
            self.commands = []
            self.tools = []
            self.hooks = []

        def register_command(self, *args, **kwargs):
            self.commands.append((args, kwargs))

        def register_tool(self, *args, **kwargs):
            self.tools.append((args, kwargs))

        def register_hook(self, *args, **kwargs):
            self.hooks.append((args, kwargs))

    ctx = FakeCtx()
    plugin.register(ctx)

    assert ctx.commands[0][0][0] == "odoo-hedge-server"
    assert "升级" in ctx.commands[0][1]["args_hint"]
    assert ctx.tools == []
    assert ctx.hooks[0][0][0] == "pre_gateway_dispatch"


@pytest.mark.asyncio
async def test_command_without_args_creates_thread_and_prompts(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(_event("/odoo-hedge-server"), _gateway(adapter))

    assert len(adapter.client.messages) == 2
    root = adapter.client.messages[0]
    reply = adapter.client.messages[1]
    assert "Odoo Hedge Server 操作线程已开启" in root["text"]
    assert "创建、升级、销毁或列状态" in root["text"]
    assert reply["thread_ts"] == root["ts"]
    assert "创建默认值" in reply["text"]
    assert "升级 demo-a 到 2026.6.2" in reply["text"]
    assert not adapter.handled_events
    assert root["ts"] in adapter._bot_message_ts
    assert root["ts"] in adapter._mentioned_threads
    assert adapter.pop_slash_context_count == 1
    adapter._send_slash_ephemeral.assert_not_awaited()

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    marker = next(iter(state["threads"].values()))
    assert marker["workflow"] == "agent"
    assert marker["thread_ts"] == root["ts"]


@pytest.mark.asyncio
async def test_command_with_args_injects_initial_thread_event(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建默认值 slug demo-a"),
        _gateway(adapter),
    )

    assert len(adapter.client.messages) == 1
    root = adapter.client.messages[0]
    assert "初始请求：创建默认值 slug demo-a" in root["text"]
    assert len(adapter.handled_events) == 1
    initial = adapter.handled_events[0]
    assert initial.text == "创建默认值 slug demo-a"
    assert initial.source.thread_id == root["ts"]
    assert initial.auto_skill == "odoo-hedge-server"
    assert "Slack requester: Alice" in initial.channel_prompt
    assert initial.raw_message["thread_ts"] == root["ts"]
    assert adapter.pop_slash_context_count == 1


@pytest.mark.asyncio
async def test_command_accepts_slack_response_object(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    adapter.client.response_wrapper = FakeSlackResponse

    await plugin._handle_odoo_event(_event("/odoo-hedge-server"), _gateway(adapter))

    root = adapter.client.messages[0]
    reply = adapter.client.messages[1]
    assert reply["thread_ts"] == root["ts"]
    assert (tmp_path / "state.json").exists()


@pytest.mark.asyncio
async def test_active_thread_hook_allows_gateway_and_marks_skill(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_odoo_event(_event("/odoo-hedge-server"), gateway)
    thread_ts = adapter.client.messages[0]["ts"]

    followup = _event("创建默认值", thread_id=thread_ts)
    result = plugin._pre_gateway_dispatch(followup, gateway)

    assert result == {"action": "allow"}
    assert followup.auto_skill == "odoo-hedge-server"
    assert "Slack requester: Alice" in followup.channel_prompt


def test_pre_gateway_dispatch_does_not_bypass_auth(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")

    result = plugin._pre_gateway_dispatch(
        _event("/odoo-hedge-server 创建默认值"),
        _gateway(FakeSlackAdapter(), authorized=False),
    )

    assert result == {"action": "allow"}
