"""Slack-thread routing tests for send_message."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from tools.send_message_tool import _send_to_platform, send_message_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def test_current_slack_thread_is_inherited_for_same_channel():
    slack_cfg = SimpleNamespace(enabled=True, token="xoxb-test", extra={})
    config = SimpleNamespace(
        platforms={Platform.SLACK: slack_cfg},
        get_home_channel=lambda _platform: None,
    )

    def fake_session_env(name, default=""):
        return {
            "HERMES_SESSION_PLATFORM": "slack",
            "HERMES_SESSION_CHAT_ID": "C123ABCDEF",
            "HERMES_SESSION_THREAD_ID": "1781507965.625969",
            "HERMES_SESSION_USER_ID": "U123",
        }.get(name, default)

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("gateway.session_context.get_session_env", side_effect=fake_session_env), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
         patch("gateway.mirror.mirror_to_session", return_value=True) as mirror_mock:
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "slack:C123ABCDEF",
                    "message": "hello",
                }
            )
        )

    assert result["success"] is True
    send_mock.assert_awaited_once_with(
        Platform.SLACK,
        slack_cfg,
        "C123ABCDEF",
        "hello",
        thread_id="1781507965.625969",
        media_files=[],
        force_document=False,
    )
    mirror_mock.assert_called_once_with(
        "slack",
        "C123ABCDEF",
        "hello",
        source_label="slack",
        thread_id="1781507965.625969",
        user_id="U123",
    )


def test_current_slack_thread_is_not_inherited_for_other_channel():
    slack_cfg = SimpleNamespace(enabled=True, token="xoxb-test", extra={})
    config = SimpleNamespace(
        platforms={Platform.SLACK: slack_cfg},
        get_home_channel=lambda _platform: None,
    )

    def fake_session_env(name, default=""):
        return {
            "HERMES_SESSION_PLATFORM": "slack",
            "HERMES_SESSION_CHAT_ID": "C123ABCDEF",
            "HERMES_SESSION_THREAD_ID": "1781507965.625969",
        }.get(name, default)

    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("gateway.session_context.get_session_env", side_effect=fake_session_env), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "slack:C999ABCDEF",
                    "message": "hello",
                }
            )
        )

    assert result["success"] is True
    send_mock.assert_awaited_once_with(
        Platform.SLACK,
        slack_cfg,
        "C999ABCDEF",
        "hello",
        thread_id=None,
        media_files=[],
        force_document=False,
    )


def test_send_to_platform_passes_slack_thread_id_to_sender():
    send = AsyncMock(return_value={"success": True, "message_id": "1"})
    slack_cfg = SimpleNamespace(enabled=True, token="***", extra={})

    from gateway.platform_registry import platform_registry
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    slack_entry = platform_registry.get("slack")
    original_sender = slack_entry.standalone_sender_fn

    async def fake_send(pconfig, chat_id, message, *, thread_id=None, **_kwargs):
        return await send(pconfig, chat_id, message, thread_id=thread_id)

    try:
        slack_entry.standalone_sender_fn = fake_send
        result = asyncio.run(
            _send_to_platform(
                Platform.SLACK,
                slack_cfg,
                "C123",
                "hello",
                thread_id="1781507965.625969",
            )
        )
    finally:
        slack_entry.standalone_sender_fn = original_sender

    assert result["success"] is True
    send.assert_awaited_once_with(
        slack_cfg,
        "C123",
        "hello",
        thread_id="1781507965.625969",
    )


def test_send_slack_includes_thread_ts_in_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"ok": True, "ts": "1781507999.000001"}

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["kwargs"] = kwargs
            return FakeResponse()

    fake_aiohttp = SimpleNamespace(
        ClientSession=FakeSession,
        ClientTimeout=lambda total: SimpleNamespace(total=total),
    )

    from plugins.platforms.slack.adapter import _standalone_send

    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
    with patch("gateway.platforms.base.resolve_proxy_url", return_value=None), \
         patch("gateway.platforms.base.proxy_kwargs_for_aiohttp", return_value=({}, {})):
        result = asyncio.run(
            _standalone_send(
                SimpleNamespace(token="xoxb-test"),
                "C123ABCDEF",
                "hello",
                thread_id="1781507965.625969",
            )
        )

    assert result["success"] is True
    assert result["thread_id"] == "1781507965.625969"
    assert captured["json"] == {
        "channel": "C123ABCDEF",
        "text": "hello",
        "mrkdwn": True,
        "thread_ts": "1781507965.625969",
    }
