"""Offline wire test: real plugin discovery, Slack adapter, RPC client and SQLite.

Only the external Slack API and Codex daemon are test servers. No model calls,
credentials, developer config or live Slack messages are used.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
import yaml
from websockets.sync.server import unix_serve

from tests.plugins.test_codex_slack_plugin import FakeSlackClient, _event


class Daemon:
    def __init__(self):
        self.threads = {}
        self.requests = []
        self.prompts = []
        self.approvals = []
        self.modes = {}
        self.turn_modes = []

    def handle(self, ws):
        for raw in ws:
            request = json.loads(raw)
            if "id" not in request:
                continue
            method, params = request["method"], request.get("params", {})
            self.requests.append((method, params))
            result = {}
            if method == "thread/start":
                thread_id = f"thread-{len(self.threads) + 1}"
                self.threads[thread_id] = {
                    "id": thread_id, "cwd": params["cwd"], "source": "appServer",
                    "createdAt": int(time.time()), "updatedAt": int(time.time()),
                    "status": {"type": "idle"}, "turns": [],
                }
                self.modes[thread_id] = {"mode": "default", "settings": {
                    "model": "test-codex-model", "reasoning_effort": "high", "developer_instructions": None,
                }}
                result = {"thread": self.threads[thread_id], "model": "test-codex-model",
                          "reasoningEffort": "high", "collaborationMode": self.modes[thread_id]}
            elif method in {"thread/read", "thread/resume"}:
                result = {"thread": self.threads[params["threadId"]], "model": "test-codex-model",
                          "reasoningEffort": "high", "collaborationMode": self.modes[params["threadId"]]}
            elif method == "thread/settings/update":
                assert set(params) == {"threadId", "collaborationMode"}
                self.modes[params["threadId"]] = params["collaborationMode"]
            elif method == "thread/name/set":
                assert set(params) == {"threadId", "name"}
                self.threads[params["threadId"]]["name"] = params["name"]
            elif method == "turn/interrupt":
                for turn in self.threads[params["threadId"]]["turns"]:
                    if turn["id"] == params["turnId"]:
                        turn["status"] = "interrupted"
            elif method == "thread/list":
                threads = [thread for thread in self.threads.values()
                           if thread.get("archived", False) == params.get("archived", False)]
                offset = int(params.get("cursor", 0))
                end = offset + params["limit"]
                result = {"data": threads[offset:end], "nextCursor": str(end) if end < len(threads) else None}
            elif method in {"thread/archive", "thread/unarchive"}:
                self.threads[params["threadId"]]["archived"] = method == "thread/archive"
            elif method == "thread/turns/list":
                turns = list(reversed(self.threads[params["threadId"]]["turns"]))
                offset = int(params.get("cursor", 0))
                end = offset + params["limit"]
                result = {"data": turns[offset:end], "nextCursor": str(end) if end < len(turns) else None}
            elif method == "turn/start":
                thread = self.threads[params["threadId"]]
                self.prompts.append(params["input"][0]["text"])
                self.turn_modes.append(self.modes[params["threadId"]]["mode"])
                turn = {"id": f"turn-{len(thread['turns']) + 1}", "status": "inProgress", "items": []}
                thread["turns"].append(turn)
                thread["status"] = {"type": "active"}
                result = {"turn": turn}
            ws.send(json.dumps({"id": request["id"], "result": result}))
            if method != "turn/start":
                continue
            note = {"threadId": thread["id"], "turnId": turn["id"]}
            ws.send(json.dumps({"method": "item/started", "params": {
                **note, "item": {"type": "commandExecution", "id": "exec", "command": "git status"},
            }}))
            ws.send(json.dumps({"id": "approval", "method": "item/commandExecution/requestApproval",
                                "params": {**note, "command": "git status", "cwd": thread["cwd"]}}))
            approval = json.loads(ws.recv(timeout=10))
            self.approvals.append(approval)
            turn.update(status="completed", completedAt=int(time.time()), items=[{
                "type": "agentMessage", "id": "answer", "text": "wire answer", "phase": "final_answer",
            }])
            thread["status"] = {"type": "idle"}
            ws.send(json.dumps({"method": "item/completed", "params": {**note, "item": turn["items"][0]}}))
            ws.send(json.dumps({"method": "turn/completed", "params": {**note, "turn": turn}}))


class SlackAPI(FakeSlackClient):
    async def users_info(self, **kwargs):
        return {"user": {"real_name": "Test user"}}

    async def conversations_replies(self, **kwargs):
        return {"messages": [{"ts": kwargs["ts"], "user": "U_BOT", "text": "previous thread content"}]}


async def eventually(predicate):
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_slack_create_approve_resume_and_terminal_sync_after_restart(tmp_path, monkeypatch):
    import hermes_cli.plugins as plugins
    from gateway.config import HomeChannel, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from plugins.platforms.slack.adapter import SlackAdapter

    socket_path = str(tmp_path / "rpc.sock")
    daemon = Daemon()
    home = tmp_path / "hermes"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {
        "enabled": ["codex-slack"], "codex-slack": {
            "default_cwd": str(repo), "allowed_roots": [str(tmp_path)],
            "shared_daemon": True, "daemon_socket": socket_path,
        },
    }}))
    with unix_serve(daemon.handle, socket_path) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        plugins.discover_plugins()
        module = plugins.get_plugin_manager()._plugins["codex-slack"].module
        assert module is not None
        manager = module._manager()
        from hermes_cli.commands import slack_app_manifest, slack_subcommand_map
        commands = slack_app_manifest()["features"]["slash_commands"]
        assert any(entry["command"] == "/odoo-hedge-codex" for entry in commands)
        assert slack_subcommand_map()["odoo-hedge-codex"] == "/odoo-hedge-codex"
        api = SlackAPI()
        adapter = SlackAdapter(PlatformConfig(enabled=True, extra={"free_response_channels": ["C_HOME"]}))
        adapter._bot_user_id = "U_BOT"
        adapter._app = SimpleNamespace(client=api)
        gateway = GatewayRunner.__new__(GatewayRunner)
        gateway.adapters = {Platform.SLACK: adapter}
        gateway.config = SimpleNamespace(get_home_channel=lambda _: HomeChannel(platform=Platform.SLACK, chat_id="C_HOME", name="Test"))
        gateway.session_store = None
        # Keep both real inbound adapter normalization and gateway plugin dispatch;
        # skip only BasePlatformAdapter's unrelated Hermes-agent scheduling.
        adapter.handle_message = gateway._handle_message
        async def project_slash(text=""):
            await adapter._handle_slash_command({
                "command": "/odoo-hedge-codex", "text": text, "channel_id": "C_HOME",
                "user_id": "U_ME", "team_id": "T1",
            })
            await asyncio.gather(*manager._handlers)
        try:
            await plugins.invoke_hook_async("on_gateway_start", gateway=gateway)
            await project_slash("new")
            binding = manager.store.by_codex("thread-1")
            assert binding is not None, api.ephemeral
            assert binding.cwd == str(repo)
            assert manager.store.path.parent == home / "state"

            async def thread_command(text):
                await adapter._handle_slack_message({
                    "text": text, "channel": "C_HOME", "user": "U_ME", "team": "T1",
                    "thread_ts": binding.thread_ts, "ts": str(time.time()), "channel_type": "channel",
                })
                await asyncio.gather(*manager._handlers)

            await thread_command("!mode plan")
            assert daemon.modes["thread-1"]["mode"] == "plan"
            await thread_command("!mode")
            assert "Plan" in api.messages[-1]["text"]
            await thread_command("!mode default")
            assert daemon.modes["thread-1"]["mode"] == "default"
            assert daemon.modes["thread-1"]["settings"]["model"] == "test-codex-model"
            assert daemon.modes["thread-1"]["settings"]["reasoning_effort"] == "high"
            assert not daemon.prompts  # Controls never invoke a model.
            assert any(method == "initialize" and params.get("capabilities", {}).get("experimentalApi")
                       for method, params in daemon.requests)

            await thread_command("!title Odoo &amp; 库存")
            assert daemon.threads["thread-1"]["name"] == "Odoo & 库存"
            await thread_command("!title")
            assert "Odoo &amp; 库存" in api.messages[-1]["text"]
            await project_slash()
            assert any(element.get("text", {}).get("text", "").startswith("Odoo & 库存 · ")
                       for block in api.ephemeral[-1]["blocks"] if block["type"] == "actions"
                       for element in block["elements"])
            assert not daemon.prompts

            await adapter._handle_slack_message({
                "text": "do the work", "channel": "C_HOME", "user": "U_ME", "team": "T1",
                "thread_ts": binding.thread_ts, "ts": "200.001", "channel_type": "channel",
            })
            await eventually(lambda: manager._approvals)
            token = next(iter(manager._approvals))
            await manager.handle_approval_action(module.ACTION_APPROVE_ONCE,
                {"channel": {"id": "C_HOME"}, "user": {"id": "U_ME"}, "team": {"id": "T1"}},
                {"value": token})
            await eventually(lambda: manager.store.progress("thread-1", "turn-1"))
            assert daemon.prompts == ["do the work"]  # No Slack transcript injection.
            assert daemon.turn_modes == ["default"]
            assert daemon.approvals[0]["result"] == {"decision": "accept"}
            assert any(message["text"] == "wire answer" and message.get("thread_ts") == binding.thread_ts
                       for message in api.messages)

            await gateway._handle_message(_event("/codex resume thread-1"))
            await asyncio.gather(*manager._handlers)
            assert manager.store.by_codex("thread-1").thread_ts == binding.thread_ts
            assert len([message for message in api.messages if not message.get("thread_ts")]) == 1

            # Stop Hermes, then a terminal client completes a new turn. Persisted
            # checkpoints recover it on startup without an incoming Slack message.
            await plugins.invoke_hook_async("on_gateway_stop", gateway=gateway)
            daemon.threads["thread-1"]["turns"].append({
                "id": "terminal-turn", "status": "completed", "completedAt": int(time.time()) + 1,
                "items": [{"type": "agentMessage", "id": "terminal-answer", "text": "terminal finished"}],
            })
            # Also cover history frames larger than websockets' default 1 MiB.
            daemon.threads["thread-1"]["preview"] = "x" * 1_100_000
            module._MANAGER = None
            await plugins.invoke_hook_async("on_gateway_start", gateway=gateway)
            manager = module._manager()
            await eventually(lambda: any(message["text"] == "terminal finished" for message in api.messages))
            await manager._sync_binding(binding)
            assert sum(message["text"] == "terminal finished" for message in api.messages) == 1
            assert daemon.prompts == ["do the work"]

            await adapter._handle_slack_message({
                "text": "!status", "channel": "C_HOME", "user": "U_ME", "team": "T1",
                "thread_ts": binding.thread_ts, "ts": "201.001", "channel_type": "channel",
            })
            await asyncio.gather(*manager._handlers)
            assert "状态：" in api.messages[-1]["text"]
            assert daemon.prompts == ["do the work"]

            await thread_command("!stop")
            assert manager.store.by_codex("thread-1").sync_paused
            await thread_command("!title Paused Work")
            assert daemon.threads["thread-1"]["name"] == "Paused Work"
            assert manager.store.by_codex("thread-1").sync_paused
            await plugins.invoke_hook_async("on_gateway_stop", gateway=gateway)
            daemon.threads["thread-1"]["turns"].append({
                "id": "paused-turn", "status": "completed", "completedAt": int(time.time()),
                "items": [{"type": "agentMessage", "text": "paused answer must not replay"}],
            })
            module._MANAGER = None
            await plugins.invoke_hook_async("on_gateway_start", gateway=gateway)
            manager = module._manager()
            before = len(api.messages)
            await manager._sync_binding(binding)
            assert len(api.messages) == before
            assert manager.store.by_codex("thread-1").sync_paused
            await thread_command("!title")
            assert "Paused Work" in api.messages[-1]["text"]
            await thread_command("!resume")
            await manager._sync_binding(binding)
            assert not any(m["text"] == "paused answer must not replay" for m in api.messages)
            assert not manager.store.by_codex("thread-1").sync_paused

            # Real discovered button handlers -> real RPC -> real binding store.
            async def click(label):
                action = next(element for block in api.ephemeral[-1]["blocks"] if block["type"] == "actions"
                              for element in block["elements"] if element["action_id"].startswith(f"codex_slack_{label.lower()}:"))
                handlers = plugins.get_plugin_manager().get_slack_action_handlers()
                callback = next(callback for pattern, callback, name in handlers
                                if name == "codex-slack" and hasattr(pattern, "match") and pattern.match(action["action_id"]))
                acknowledged = []
                async def ack():
                    acknowledged.append(True)
                await callback(ack, {"channel": {"id": "C_HOME"}, "user": {"id": "U_ME"}, "team": {"id": "T1"}}, action)
                assert acknowledged == [True]

            await project_slash()
            await click("Archive")
            assert daemon.threads["thread-1"]["archived"]
            assert manager.store.by_codex("thread-1").state == "archived"
            await project_slash("sessions archived")
            await click("Resume")
            assert not daemon.threads["thread-1"]["archived"]
            assert manager.store.by_codex("thread-1").thread_ts == binding.thread_ts
            assert manager.store.by_codex("thread-1").state == "active"
            assert not any(method == "thread/delete" for method, _ in daemon.requests)
            assert daemon.prompts == ["do the work"]
        finally:
            await plugins.invoke_hook_async("on_gateway_stop", gateway=gateway)
            server.shutdown()
            server_thread.join(timeout=5)
