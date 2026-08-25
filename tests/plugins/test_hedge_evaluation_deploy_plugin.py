"""Tests for the fixed evaluation-environment Slack workflow."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import Platform


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "plugins" / "hedge-evaluation-deploy" / "__init__.py"
SKILL_PATH = REPO_ROOT / "skills" / "hedge-evaluation-deploy" / "SKILL.md"


def _load_plugin():
    module_name = "test_hedge_evaluation_deploy_plugin_module"
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
        timestamp = f"{self._counter}.000"
        self.messages.append({**kwargs, "ts": timestamp})
        return {"ok": True, "ts": timestamp}


class FakeSlackAdapter:
    def __init__(self):
        self.client = FakeSlackClient()
        self.handled_events = []
        self._bot_message_ts = set()
        self._mentioned_threads = set()
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
        chat_name="private-evaluation",
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


def test_skill_defines_sensitive_confirmed_mcp_workflow():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "确认部署测评环境" in skill
    assert "current validated submission" in skill
    assert "ctp_auth_code" in skill
    assert "Never paste it back" in skill
    assert "call a memory tool" in skill
    assert "exactly one Slack code block" in skill
    assert "Authoritative broker_json" in skill
    assert "Preserve it exactly" in skill
    assert "does not guarantee that the control plane accepts an empty" in skill
    assert "latest `origin/master`" in skill
    assert "`commit` alone" in skill
    assert "both `branch` and `commit`" in skill
    assert "`tag` alone" in skill
    assert "`ctp_api_test_mode` must be a JSON boolean" in skill
    assert "references/retry-after-failed-deploy.md" in skill
    assert "complete confirmed deployment submission" in (
        SKILL_PATH.parent.joinpath("references/retry-after-failed-deploy.md")
        .read_text(encoding="utf-8")
    )
    for tool_name in (
        "evaluation_healthz",
        "check_evaluation_environment_status",
        "verify_evaluation_environment",
        "deploy_evaluation_environment",
        "get_evaluation_operation",
        "list_evaluation_operations",
        "wait_evaluation_operation",
        "cancel_evaluation_operation",
    ):
        assert f"`{tool_name}`" in skill


def test_extracts_broker_json_verbatim_from_one_slack_code_block():
    plugin = _load_plugin()
    code_content = (
        '[{"trading_fronts":["tcp://front.example:41205"],'
        '"literal":"<keep-this>"}]\n'
    )
    event = _event("Slack plain text contains <tcp://front.example:41205>")
    event.raw_message["blocks"] = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [
                        {"type": "text", "text": code_content},
                    ],
                }
            ],
        }
    ]

    blocks = plugin._valid_broker_json_code_blocks(event)

    assert blocks == [code_content]


def test_plain_json_and_multiple_code_blocks_are_not_authoritative():
    plugin = _load_plugin()
    plain = _event('[{"id":"plain"}]')
    multiple = _event("two blocks")
    multiple.raw_message["blocks"] = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": "[]"}],
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": '[{"id":"two"}]'}],
                },
            ],
        }
    ]

    assert plugin._valid_broker_json_code_blocks(plain) == []
    assert len(plugin._valid_broker_json_code_blocks(multiple)) == 2


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

    assert ctx.commands[0][0][0] == "hedge-evaluation-deploy"
    assert ctx.commands[0][1]["platforms"] == ("slack",)
    assert "状态" in ctx.commands[0][1]["args_hint"]
    assert ctx.tools == []
    assert ctx.hooks[0][0][0] == "pre_gateway_dispatch"


@pytest.mark.asyncio
async def test_command_without_args_creates_private_workflow_thread(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()

    await plugin._handle_evaluation_event(
        _event("/hedge-evaluation-deploy"),
        _gateway(adapter),
    )

    assert len(adapter.client.messages) == 2
    root, reply = adapter.client.messages
    assert "测评环境部署线程已开启" in root["text"]
    assert reply["thread_ts"] == root["ts"]
    assert "确认部署测评环境" in reply["text"]
    assert "Hedge V2 版本" in reply["text"]
    assert "ctp_api_test_mode" in reply["text"]
    assert "私有 Slack 频道" in reply["text"]
    assert not adapter.handled_events
    assert root["ts"] in adapter._bot_message_ts
    assert root["ts"] in adapter._mentioned_threads
    assert adapter.pop_slash_context_count == 1


@pytest.mark.asyncio
async def test_initial_broker_json_is_not_echoed_or_persisted(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    secret = "SUPER-SECRET-AUTH-CODE"
    payload = f'部署 [{{"ctp_auth_code":"{secret}"}}]'

    await plugin._handle_evaluation_event(
        _event(f"/hedge-evaluation-deploy {payload}"),
        _gateway(adapter),
    )

    assert len(adapter.client.messages) == 1
    root = adapter.client.messages[0]
    assert "敏感内容不回显" in root["text"]
    assert secret not in root["text"]

    state_text = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert secret not in state_text
    assert "raw_text" not in state_text

    assert len(adapter.handled_events) == 1
    initial = adapter.handled_events[0]
    assert initial.text.endswith(payload)
    assert initial.text.startswith("[Sensitive evaluation-deployment input.")
    assert secret not in initial.text[:80]
    assert initial.auto_skill == "hedge-evaluation-deploy"
    assert "Slack requester: Alice" in initial.channel_prompt
    assert "Only this requester may confirm" in initial.channel_prompt
    assert secret not in json.dumps(initial.raw_message)
    assert initial.source.thread_id == root["ts"]


@pytest.mark.asyncio
async def test_active_thread_is_bound_to_skill_and_requester(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_evaluation_event(_event("/hedge-evaluation-deploy"), gateway)
    thread_ts = adapter.client.messages[0]["ts"]

    followup = _event("测评环境现在是什么状态？", thread_id=thread_ts)
    followup.reply_to_text = '{"ctp_auth_code":"must-not-be-logged"}'
    result = plugin._pre_gateway_dispatch(followup, gateway)

    assert result == {"action": "allow"}
    assert followup.text.startswith("[Sensitive evaluation-deployment input.")
    assert "测评环境现在是什么状态？" in followup.text
    assert followup.reply_to_text == "[sensitive evaluation-deployment thread message]"
    assert followup.auto_skill == "hedge-evaluation-deploy"
    assert "Slack requester: Alice" in followup.channel_prompt
    assert "do not echo raw JSON" in followup.channel_prompt
    assert "Valid top-level JSON-array code blocks in the current message: 0" in (
        followup.channel_prompt
    )


@pytest.mark.asyncio
async def test_active_thread_uses_exact_code_block_instead_of_slack_plain_text(
    tmp_path, monkeypatch
):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")
    adapter = FakeSlackAdapter()
    gateway = _gateway(adapter)

    await plugin._handle_evaluation_event(_event("/hedge-evaluation-deploy"), gateway)
    thread_ts = adapter.client.messages[0]["ts"]
    exact_code = '[{"trading_fronts":["tcp://front.example:41205"]}]\n'
    followup = _event(
        '```\n[{"trading_fronts":["<tcp://front.example:41205>"]}]\n```',
        thread_id=thread_ts,
    )
    followup.raw_message["blocks"] = [
        {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": exact_code}],
                }
            ],
        }
    ]

    result = plugin._pre_gateway_dispatch(followup, gateway)

    assert result == {"action": "allow"}
    assert followup.text.endswith(exact_code)
    assert "<tcp://" not in followup.text
    assert "Authoritative broker_json extracted verbatim" in followup.text
    assert "Valid top-level JSON-array code blocks in the current message: 1" in (
        followup.channel_prompt
    )


def test_pre_gateway_dispatch_does_not_bypass_auth(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_state_path", lambda: tmp_path / "state.json")

    result = plugin._pre_gateway_dispatch(
        _event("/hedge-evaluation-deploy 状态"),
        _gateway(FakeSlackAdapter(), authorized=False),
    )

    assert result == {"action": "allow"}
    assert not (tmp_path / "state.json").exists()
