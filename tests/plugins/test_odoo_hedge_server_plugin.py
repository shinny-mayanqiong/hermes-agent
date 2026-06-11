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

    async def chat_postMessage(self, **kwargs):
        self._counter += 1
        ts = f"{self._counter}.000"
        self.messages.append({**kwargs, "ts": ts})
        return {"ok": True, "ts": ts}


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


def test_parse_chinese_create_request_with_version():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建一个 17.0 服务")

    assert parsed["intent"] == "create"
    assert parsed["version"] == "17.0"


def test_parse_chinese_create_request_without_version():
    plugin = _load_plugin()

    parsed = plugin._parse_request("创建一个服务")

    assert parsed["intent"] == "create"
    assert parsed["version"] == ""


def test_default_config_enables_plugin():
    from hermes_cli.config import DEFAULT_CONFIG

    assert "odoo-hedge-server" in DEFAULT_CONFIG["plugins"]["enabled"]


@pytest.mark.asyncio
async def test_command_creates_thread_and_asks_for_missing_version(tmp_path, monkeypatch):
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
    assert "请在此 thread 回复想要部署的 Odoo 版本" in reply["text"]
    assert root["ts"] in adapter._bot_message_ts
    adapter._send_slash_ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_registers_gateway_thread_session(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
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


@pytest.mark.asyncio
async def test_thread_followup_collects_version_and_echoes_intent(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_odoo_event(
        _event("/odoo-hedge-server 创建一个服务"),
        gateway,
    )
    thread_ts = adapter.client.messages[0]["ts"]

    await plugin._handle_odoo_event(
        _event("17.0", thread_id=thread_ts),
        gateway,
    )

    followup = adapter.client.messages[-1]
    assert followup["thread_ts"] == thread_ts
    assert "用户：Alice (`U123`)" in followup["text"]
    assert "意图：创建服务" in followup["text"]
    assert "部署版本：17.0" in followup["text"]
