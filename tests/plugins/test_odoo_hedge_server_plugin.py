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

    def _get_client(self, chat_id):
        del chat_id
        return self.client

    async def _resolve_user_name(self, user_id, chat_id=""):
        del user_id, chat_id
        return "Alice"

    def _pop_slash_context(self, chat_id):
        del chat_id
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


def _sandbox_record(**overrides):
    record = {
        "id": "sbx-1",
        "slug": "odoo-demo-1",
        "owner": "Alice",
        "status": "running",
        "db_name": "odoo_demo_1",
        "url": "http://127.0.0.1:18081",
        "host_port": 18081,
        "container_name": "odoo-demo-1",
        "filestore_path": "/tmp/filestore",
        "config_path": "/tmp/odoo.conf",
        "branch": None,
        "commit": None,
        "tag": None,
        "image_tag": "hedge-test",
        "image": "odoo:test",
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
        "last_error": None,
        "sync_defaults_status": "pending",
        "sync_defaults_error": None,
        "sync_defaults_updated_at": None,
    }
    record.update(overrides)
    return record


def test_default_config_enables_plugin():
    from hermes_cli.config import DEFAULT_CONFIG

    assert "odoo-hedge-server" in DEFAULT_CONFIG["plugins"]["enabled"]
    assert DEFAULT_CONFIG["odoo_hedge_server"]["api_base_url"] == "http://127.0.0.1:18080"
    assert DEFAULT_CONFIG["odoo_hedge_server"]["timeout_seconds"] == 1200


def test_create_body_preserves_custom_slug_for_default_target():
    plugin = _load_plugin()

    body = plugin._create_body(
        {
            "user_name": "Alice",
            "slug": "demo-a",
            "use_default": "true",
            "branch": "17.0",
            "commit": "abcdef1",
            "tag": "2026.6.1",
        }
    )

    assert body == {"owner": "Alice", "slug": "demo-a"}


def test_register_exposes_odoo_toolset_and_slack_hook():
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
    assert {call[1]["toolset"] for call in ctx.tools} == {"odoo_hedge_server"}
    assert {call[1]["name"] for call in ctx.tools} == {
        "odoo_sandbox_create",
        "odoo_sandbox_provision_sync_defaults",
        "odoo_sandbox_list",
        "odoo_sandbox_get",
        "odoo_sandbox_destroy",
    }
    assert ctx.hooks[0][0][0] == "pre_gateway_dispatch"


def test_create_tool_posts_owner_slug_and_default_target(monkeypatch):
    plugin = _load_plugin()
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path, body))
        return _sandbox_record(slug=body["slug"], branch=None)

    monkeypatch.setattr(plugin, "_api_request_sync", fake_api)

    payload = json.loads(
        plugin._odoo_sandbox_create_tool({"owner": "Alice", "slug": "demo-a"})
    )

    assert payload["ok"] is True
    assert payload["data"]["slug"] == "demo-a"
    assert calls == [("POST", "/sandboxes", {"owner": "Alice", "slug": "demo-a"})]


def test_create_tool_returns_backend_error_details(monkeypatch):
    plugin = _load_plugin()

    def fake_api(method, path, body=None):
        del method, path, body
        raise plugin.SandboxApiError(
            "Docker build failed while ensuring sandbox image.",
            status=502,
            code="docker_image_build_failed",
            details={"image_ensure": {"error": "Dockerfile.sandbox not found"}},
        )

    monkeypatch.setattr(plugin, "_api_request_sync", fake_api)

    payload = json.loads(plugin._odoo_sandbox_create_tool({"owner": "Alice"}))

    assert payload["ok"] is False
    assert payload["status"] == 502
    assert payload["code"] == "docker_image_build_failed"
    assert payload["details"]["image_ensure"]["error"] == "Dockerfile.sandbox not found"


def test_provision_get_list_and_destroy_tools_call_expected_paths(monkeypatch):
    plugin = _load_plugin()
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/sandboxes":
            return [_sandbox_record()]
        return _sandbox_record(slug="demo-a")

    monkeypatch.setattr(plugin, "_api_request_sync", fake_api)

    assert json.loads(plugin._odoo_sandbox_provision_sync_defaults_tool({"slug": "demo-a"}))["ok"]
    assert json.loads(plugin._odoo_sandbox_get_tool({"slug": "demo-a"}))["ok"]
    assert json.loads(plugin._odoo_sandbox_list_tool({}))["ok"]
    assert json.loads(plugin._odoo_sandbox_destroy_tool({"slug": "demo-a"}))["ok"]
    assert calls == [
        ("POST", "/sandboxes/demo-a/provision-sync-defaults", None),
        ("GET", "/sandboxes/demo-a", None),
        ("GET", "/sandboxes", None),
        ("POST", "/sandboxes/demo-a/destroy", None),
    ]


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
    assert reply["thread_ts"] == root["ts"]
    assert "创建默认值" in reply["text"]
    assert not adapter.handled_events
    assert root["ts"] in adapter._bot_message_ts
    assert root["ts"] in adapter._mentioned_threads
    adapter._send_slash_ephemeral.assert_awaited_once()

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
