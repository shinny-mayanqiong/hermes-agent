"""Explicit controls for a bound Slack thread and its automatic delivery."""

from __future__ import annotations

import asyncio

from .progress import activity_text, turn_progress


class SessionControls:
    def _control_lock(self, binding):
        return self._control_locks.setdefault(binding.codex_thread_id, asyncio.Lock())

    async def mode(self, binding, argument: str = "") -> None:
        if argument not in {"", "plan", "default"}:
            await self._post_message(binding.channel_id,
                "用法：`!mode` 查看模式；`!mode plan` 规划；`!mode default` 执行。",
                thread_ts=binding.thread_ts)
            return
        runtime = self._runtime(binding)
        if runtime.active or runtime.stopping:
            await self._post_message(binding.channel_id,
                "任务正在运行，请先 `!stop`，再查看或切换模式。", thread_ts=binding.thread_ts)
            return
        try:
            self._ensure_reaper()
            if not await self._ensure_session(runtime):
                return
            if argument:
                settings = await asyncio.to_thread(runtime.session.set_collaboration_mode, argument)
            else:
                settings = await asyncio.to_thread(runtime.session.read_settings)
            name = (settings.get("collaborationMode") or {}).get("mode")
            label = {"plan": "Plan（规划）", "default": "Default（执行）"}.get(name, "未知（Codex 未返回模式）")
            text = f"{'已切换为' if argument else '当前模式：'} *{label}*。"
            if argument:
                text += "下一轮任务使用此模式；切换本身不会启动任务。"
            current = self.store.by_codex(binding.codex_thread_id)
            if current and current.sync_paused:
                text += "\n自动推送仍暂停；用 `!resume` 恢复后再发送任务。"
        except Exception as exc:
            text = f"无法{'切换' if argument else '读取'}模式：{exc}"
        await self._post_message(binding.channel_id, text, thread_ts=binding.thread_ts)

    async def status(self, binding) -> None:
        runtime = self._runtimes.get(binding.codex_thread_id)
        detail = ""
        if self.config.shared_daemon:
            try:
                snapshot = await asyncio.to_thread(self.catalog.read_activity, binding.codex_thread_id)
                state = activity_text(snapshot)
                turns = snapshot.get("turns") or []
                if turns:
                    detail = "\n最近进展：" + turn_progress(turns[-1])
            except Exception as exc:
                state = "无法连接 Codex（状态未知）"
                detail = f"\n{exc}"
        elif runtime and runtime.active:
            state = "运行中"
        elif runtime and runtime.session is not None:
            state = "空闲（进程已缓存）"
        else:
            state = "休眠（无运行进程）"
        current = self.store.by_codex(binding.codex_thread_id)
        sync = "已暂停（!resume 恢复）" if current and current.sync_paused else "已开启"
        queued = len(runtime.queue) if runtime else 0
        await self._post_message(binding.channel_id,
            f"状态：*{state}*\n自动推送：{sync}\nCodex ID：`{binding.codex_thread_id}`\n"
            f"目录：`{binding.cwd}`\n排队：{queued}{detail}", thread_ts=binding.thread_ts)

    async def resume_sync(self, binding) -> bool:
        current = self.store.by_codex(binding.codex_thread_id)
        if not current or current.state != "active":
            return False
        if not current.sync_paused:
            await self._post_message(binding.channel_id, "自动推送已开启。", thread_ts=binding.thread_ts)
            return True
        lock = self._mirror_locks.setdefault(binding.codex_thread_id, asyncio.Lock())
        try:
            async with lock:
                # Advance to the latest turn instead of replaying paused history.
                snapshot = (await asyncio.to_thread(self.catalog.read_activity, binding.codex_thread_id)
                            if self.config.shared_daemon else {})
                turns = snapshot.get("turns") or []
                self.store.reset_progress(binding)
                if turns:
                    latest = turns[-1]
                    self.store.save_progress(binding.codex_thread_id, latest["id"],
                                             completed=latest.get("status") != "inProgress")
                self.store.pause_sync(binding, False)
        except Exception as exc:
            await self._post_message(binding.channel_id, f"恢复失败，自动推送仍暂停：{exc}",
                                     thread_ts=binding.thread_ts)
            return False
        await self._post_message(binding.channel_id,
            "已恢复自动推送；暂停期间的历史消息不会补发。可继续发送任务。",
            thread_ts=binding.thread_ts)
        return True

    async def stop(self, binding) -> None:
        # Persist before any await: a poll already in flight must observe this.
        self.store.pause_sync(binding, True)
        runtime = self._runtimes.get(binding.codex_thread_id)
        dropped = len(runtime.queue) if runtime else 0
        if runtime:
            runtime.stopping = True
            runtime.queue.clear()
            if runtime.session:
                runtime.session.request_interrupt()
        self._deny_pending_approvals(binding)
        error = ""
        if self.config.shared_daemon:
            try:
                snapshot = await asyncio.to_thread(self.catalog.read_activity, binding.codex_thread_id)
                for turn in reversed(snapshot.get("turns") or []):
                    if turn.get("status") == "inProgress":
                        await asyncio.to_thread(self.catalog.interrupt_turn, binding.codex_thread_id, turn["id"])
                        break
            except Exception as exc:
                error = f"\n无法确认 Codex 已停止：{exc}"
        if runtime:
            worker = runtime.worker
            if worker and worker is not asyncio.current_task():
                try:
                    await asyncio.wait_for(asyncio.shield(worker), timeout=10)
                except asyncio.TimeoutError:
                    await self._close_runtime(runtime)
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            await self._close_runtime(runtime)
            self._runtimes.pop(binding.codex_thread_id, None)
            async with runtime.progress_lock:
                pass
        # Drain any already-started delivery before acknowledging the stop.
        lock = self._mirror_locks.setdefault(binding.codex_thread_id, asyncio.Lock())
        async with lock:
            pass
        suffix = f"，清空 {dropped} 条排队消息" if dropped else ""
        await self._post_message(binding.channel_id,
            f"已暂停自动推送并释放连接{suffix}；已请求中断当前任务。{error}\n"
            "暂停状态会在重启后保留。使用 `!resume` 恢复同步，再发送任务。",
            thread_ts=binding.thread_ts)
