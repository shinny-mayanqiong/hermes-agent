"""Tests for the Slack socket forwarder plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


PLUGIN_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "slack-socket-forwarder"
    / "__init__.py"
)


def _load_plugin():
    module_name = "test_slack_socket_forwarder_plugin"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_build_forward_payload_extracts_reference_fields():
    plugin = _load_plugin()

    payload = plugin._build_forward_payload(
        {
            "user": {"id": "U123"},
            "channel": {"id": "C456"},
            "container": {"message_ts": "171.123"},
        },
        {
            "action_id": "create_issue",
            "value": '{"incident_id": "INC-9"}',
        },
    )

    assert payload == {
        "action_id": "create_issue",
        "incident_id": "INC-9",
        "user_id": "U123",
        "channel_id": "C456",
        "message_ts": "171.123",
    }


def test_register_forward_action_handlers_uses_default_action_ids(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.delenv("SLACK_SOCKET_FORWARD_ACTION_IDS", raising=False)

    registered = []

    class FakeApp:
        def action(self, action_id):
            registered.append(action_id)

            def decorator(fn):
                return fn

            return decorator

    adapter = MagicMock()
    adapter._app = FakeApp()

    plugin._register_forward_action_handlers(adapter)

    assert registered == ["create_issue", "create_issue_and_pr"]
    assert adapter._app._slack_socket_forwarder_registered is True


@pytest.mark.asyncio
async def test_forward_action_posts_to_configured_target(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setenv(
        "SLACK_SOCKET_FORWARD_URL",
        "http://192.168.139.8:9000/internal/slack/socket-interactions",
    )
    monkeypatch.setenv("SLACK_SOCKET_INTERNAL_TOKEN", "secret-token")

    seen = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '{"ok": true}'

        async def json(self):
            return {"ok": True}

    class FakeSession:
        def __init__(self, *args, **kwargs):
            seen["session_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return FakeResponse()

    fake_aiohttp = MagicMock()
    fake_aiohttp.ClientTimeout = lambda **kwargs: ("timeout", kwargs)
    fake_aiohttp.ClientSession = FakeSession
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

    ack = AsyncMock()
    await plugin._handle_forward_action(
        ack,
        {
            "user": {"id": "U123"},
            "channel": {"id": "C456"},
            "container": {"message_ts": "171.123"},
        },
        {
            "action_id": "create_issue_and_pr",
            "value": '{"incident_id": "INC-10"}',
        },
    )

    ack.assert_awaited_once()
    assert seen["url"] == (
        "http://192.168.139.8:9000/internal/slack/socket-interactions"
    )
    assert seen["kwargs"]["json"] == {
        "action_id": "create_issue_and_pr",
        "incident_id": "INC-10",
        "user_id": "U123",
        "channel_id": "C456",
        "message_ts": "171.123",
    }
    assert seen["kwargs"]["headers"] == {"X-ZQ-Internal-Token": "secret-token"}
