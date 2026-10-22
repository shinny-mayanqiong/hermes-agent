"""Read-only mirroring of daemon-owned Codex turns into their Slack threads."""

from __future__ import annotations

import asyncio
import hashlib
import logging

logger = logging.getLogger(__name__)


def activity_text(thread: dict) -> str:
    status = thread.get("status") or {}
    state = status.get("type", "unknown") if isinstance(status, dict) else status
    flags = status.get("activeFlags", []) if isinstance(status, dict) else []
    if "waitingOnApproval" in flags:
        return "等待批准"
    if "waitingOnUserInput" in flags:
        return "等待输入"
    return {"active": "运行中", "idle": "空闲", "notLoaded": "休眠",
            "systemError": "异常"}.get(state, "未知")


def turn_progress(turn: dict) -> str:
    """A bounded summary; never include reasoning text or raw command output."""
    items = turn.get("items") or []
    for item in reversed(items):
        kind = item.get("type")
        if kind == "agentMessage" and item.get("text"):
            return str(item["text"])[-1800:]
        if kind == "commandExecution":
            return f"命令：{str(item.get('command') or '')[:500]}（{item.get('status', 'inProgress')}）"
        if kind == "fileChange":
            paths = [str(change.get("path", "")) for change in item.get("changes", [])]
            return "文件修改：" + ", ".join(paths)[:700]
        if kind == "mcpToolCall":
            return f"工具：{item.get('server', '')}/{item.get('tool', '')}"
        if kind == "plan":
            return str(item.get("text") or "")[-1800:]
    return "Codex 正在处理…"


def final_text(turn: dict) -> str:
    messages = [item for item in turn.get("items", [])
                if item.get("type") == "agentMessage" and item.get("text")]
    finals = [item for item in messages if item.get("phase") == "final_answer"]
    return str((finals or messages)[-1]["text"]) if messages else ""


class ProgressMirror:
    """Mixin for the plugin manager; owns no Codex execution or subscriptions.

    Polling stored turns also recovers completion events missed while Hermes was
    offline. Only the shared daemon can report another client's live state.
    """

    def start(self, gateway) -> None:
        self.gateway = gateway
        self._shutting_down = False
        if not self.config.shared_daemon or not self._home_channel() or not self._adapter():
            return
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(self._monitor_progress())

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = [task for task in (self._monitor, self._reaper) if task is not None]
        tasks.extend(self._handlers)
        for runtime in list(self._runtimes.values()):
            runtime.stopping = True
            runtime.queue.clear()
            self._deny_pending_approvals(runtime.binding)
            if runtime.worker:
                tasks.append(runtime.worker)
            # Closing a shared connection must not interrupt terminal-owned work.
            await self._close_runtime(runtime)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._runtimes.clear()
        self._monitor = self._reaper = None

    async def _monitor_progress(self) -> None:
        while True:
            try:
                for binding in self.store.in_channel(self._home_channel()):
                    try:
                        await self._sync_binding(binding)
                    except Exception:
                        # Retry on the next tick, retaining the last durable delivery.
                        logger.warning("Codex progress sync failed for %s", binding.codex_thread_id,
                                       exc_info=True)
            except Exception:
                logger.exception("Codex progress monitor failed")
            await asyncio.sleep(self.config.progress_poll_seconds)

    async def _sync_binding(self, binding, *, initialize: bool = False) -> None:
        lock = self._mirror_locks.setdefault(binding.codex_thread_id, asyncio.Lock())
        async with lock:
            await self._sync_binding_locked(binding, initialize=initialize)

    async def _sync_binding_locked(self, binding, *, initialize: bool = False) -> None:
        current = self.store.by_codex(binding.codex_thread_id)
        if not current or current.state != "active" or not self._cwd_allowed(current.cwd):
            return
        runtime = self._runtimes.get(binding.codex_thread_id)
        if runtime and runtime.active:
            return  # The Slack worker owns delivery until it saves its checkpoint.
        previous = self.store.progress(binding.codex_thread_id)
        snapshot = await asyncio.to_thread(
            self.catalog.read_activity, binding.codex_thread_id,
            after_turn_id=previous["turn_id"] if previous else "",
        )
        # A Slack turn can start while the read is in flight.
        runtime = self._runtimes.get(binding.codex_thread_id)
        if runtime and runtime.active:
            return
        turns = snapshot.get("turns") or []
        if previous:
            for index, turn in enumerate(turns):
                if turn.get("id") == previous["turn_id"]:
                    turns = turns[index:]
                    break
        for turn in turns:
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                continue
            status = turn.get("status") or "inProgress"
            done = status != "inProgress"
            saved = self.store.progress(binding.codex_thread_id, turn_id)
            if saved and saved["completed"]:
                continue
            # Seed old history on a new binding, but catch up runs started since
            # binding creation if Hermes stopped before the first polling tick.
            if not previous and done and (initialize or (turn.get("completedAt") or 0) < int(binding.created_at)):
                self.store.save_progress(binding.codex_thread_id, turn_id, completed=True)
                continue
            detail = turn_progress(turn)
            heading = {"completed": "✅ Codex 已完成。", "interrupted": "⏹ Codex 已中断。",
                       "failed": "⚠️ Codex 运行失败。"}.get(status, f"⏳ Codex {activity_text(snapshot)}")
            error = turn.get("error") or {}
            if error:
                detail = str(error.get("message") or error)[:1800]
            text = heading + ("\n" + detail if detail else "")
            digest = hashlib.sha256(text.encode()).hexdigest()
            message_ts = saved["message_ts"] if saved else ""
            if not saved or saved["digest"] != digest:
                if message_ts:
                    await self._edit_message(binding.channel_id, message_ts, text, strict=True)
                else:
                    response = await self._post_message(binding.channel_id, text, thread_ts=binding.thread_ts)
                    message_ts = str(response.get("ts") or "")
                    if not message_ts:
                        raise RuntimeError("Slack did not return a progress timestamp")
                # Save the status before sending the final so a failed send can
                # retry without creating another progress message.
                self.store.save_progress(binding.codex_thread_id, turn_id,
                                         message_ts=message_ts, digest=digest)
            if done:
                answer = final_text(turn)
                if answer:
                    await self._send_final(binding, answer)
                self.store.save_progress(binding.codex_thread_id, turn_id,
                                         message_ts=message_ts, digest=digest, completed=True)
            self.store.touch(binding)
