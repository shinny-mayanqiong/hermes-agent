"""Tests for the Odoo Hedge Server Slack workflow plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


def _gateway(adapter):
    return SimpleNamespace(
        adapters={Platform.SLACK: adapter},
        _is_user_authorized=lambda source: True,
        session_store=SimpleNamespace(get_or_create_session=MagicMock()),
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


def test_parse_chinese_create_request_with_branch_version():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建一个 17.0 服务")

    assert parsed["intent"] == "create"
    assert parsed["version"] == "17.0"
    assert parsed["branch"] == "17.0"
    assert parsed["commit"] == ""
    assert parsed["tag"] == ""


def test_parse_create_commit_prefers_commit():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建 abcdef123456")

    assert parsed["intent"] == "create"
    assert parsed["commit"] == "abcdef123456"
    assert parsed["tag"] == ""
    assert parsed["branch"] == ""


def test_parse_create_tag_three_numeric_parts():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建 2026.6.1")

    assert parsed["intent"] == "create"
    assert parsed["tag"] == "2026.6.1"
    assert parsed["commit"] == ""
    assert parsed["branch"] == ""


def test_parse_destroy_slug():
    plugin = _load_plugin()

    parsed = plugin._parse_request("销毁 odoo-demo-1")

    assert parsed["intent"] == "destroy"
    assert parsed["slug"] == "odoo-demo-1"


def test_parse_chinese_create_request_without_version():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建一个服务")

    assert parsed["intent"] == "create"
    assert parsed["version"] == ""


def test_default_config_enables_plugin():
    from hermes_cli.config import DEFAULT_CONFIG

    assert "odoo-hedge-server" in DEFAULT_CONFIG["plugins"]["enabled"]
    assert DEFAULT_CONFIG["odoo_hedge_server"]["api_base_url"] == "http://127.0.0.1:18080"


@pytest.mark.asyncio
async def test_command_creates_thread_and_asks_for_missing_target(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建一个服务"),
        _gateway(adapter),
    )

    assert len(adapter.client.messages) == 2
    root = adapter.client.messages[0]
    reply = adapter.client.messages[1]
    assert "Odoo Hedge Server 操作线程已开启" in root["text"]
    assert "初始请求：创建一个服务" in root["text"]
    assert reply["thread_ts"] == root["ts"]
    assert "请在此 thread 回复要部署的 branch、commit 或 tag" in reply["text"]
    assert root["ts"] in adapter._bot_message_ts
    adapter._send_slash_ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_accepts_slack_response_object(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    adapter.client.response_wrapper = FakeSlackResponse

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建一个服务"),
        _gateway(adapter),
    )

    root = adapter.client.messages[0]
    reply = adapter.client.messages[1]
    assert reply["thread_ts"] == root["ts"]
    assert (tmp_path / "state.json").exists()


@pytest.mark.asyncio
async def test_command_registers_gateway_thread_session(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock(return_value=_sandbox_record(branch="17.0"))
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建一个 17.0 服务"),
        gateway,
    )

    gateway.session_store.get_or_create_session.assert_called_once()
    source = gateway.session_store.get_or_create_session.call_args.args[0]
    assert source.platform == Platform.SLACK
    assert source.chat_id == "C123"
    assert source.thread_id == adapter.client.messages[0]["ts"]
    api_request.assert_awaited_once_with(
        "POST",
        "/sandboxes",
        {"owner": "Alice", "branch": "17.0"},
    )


@pytest.mark.asyncio
async def test_thread_followup_collects_commit_and_calls_create(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock(return_value=_sandbox_record(commit="abcdef123456"))
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建一个服务"),
        gateway,
    )
    thread_ts = adapter.client.messages[0]["ts"]

    await plugin._handle_odoo_event(
        _event("abcdef123456", thread_id=thread_ts),
        gateway,
    )

    followup = adapter.client.messages[-1]
    assert followup["thread_ts"] == thread_ts
    assert "已创建 Odoo sandbox" in followup["text"]
    assert "commit：`abcdef123456`" in followup["text"]
    api_request.assert_awaited_once_with(
        "POST",
        "/sandboxes",
        {"owner": "Alice", "commit": "abcdef123456"},
    )


@pytest.mark.asyncio
async def test_command_create_with_tag_calls_api(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock(return_value=_sandbox_record(tag="2026.6.1"))
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建 2026.6.1"),
        _gateway(adapter),
    )

    reply = adapter.client.messages[-1]
    assert "已创建 Odoo sandbox" in reply["text"]
    assert "tag：`2026.6.1`" in reply["text"]
    api_request.assert_awaited_once_with(
        "POST",
        "/sandboxes",
        {"owner": "Alice", "tag": "2026.6.1"},
    )


@pytest.mark.asyncio
async def test_command_status_lists_sandboxes(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock(return_value=[_sandbox_record(slug="odoo-demo-1")])
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 列状态"),
        _gateway(adapter),
    )

    reply = adapter.client.messages[-1]
    assert "当前 sandbox" in reply["text"]
    assert "`odoo-demo-1`" in reply["text"]
    api_request.assert_awaited_once_with("GET", "/sandboxes")


@pytest.mark.asyncio
async def test_thread_followup_collects_slug_and_calls_destroy(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock(return_value=_sandbox_record(status="destroyed"))
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 销毁"),
        gateway,
    )
    thread_ts = adapter.client.messages[0]["ts"]
    assert "请在此 thread 回复 sandbox slug" in adapter.client.messages[-1]["text"]

    await plugin._handle_odoo_event(
        _event("odoo-demo-1", thread_id=thread_ts),
        gateway,
    )

    reply = adapter.client.messages[-1]
    assert "已调用销毁接口" in reply["text"]
    api_request.assert_awaited_once_with("POST", "/sandboxes/odoo-demo-1/destroy")


@pytest.mark.asyncio
async def test_upgrade_reports_unsupported_api(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    api_request = AsyncMock()
    monkeypatch.setattr(plugin, "_api_request", api_request)
    adapter = FakeSlackAdapter()

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 升级 odoo-demo-1"),
        _gateway(adapter),
    )

    reply = adapter.client.messages[-1]
    assert "当前 HTTP API 未暴露升级接口" in reply["text"]
    api_request.assert_not_awaited()
