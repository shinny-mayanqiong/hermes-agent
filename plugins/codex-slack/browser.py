"""Project-scoped session cards and explicit Codex lifecycle actions."""

from __future__ import annotations

import asyncio
from datetime import datetime

from agent.transports.codex_app_server_session import CodexThreadPage

ACTION_PAGE = "codex_slack_page"
ACTION_RESUME = "codex_slack_resume"
ACTION_ARCHIVE = "codex_slack_archive"


def _date(timestamp: int) -> str:
    if not timestamp:
        return "—"
    updated = datetime.fromtimestamp(timestamp).astimezone()
    pattern = "%m-%d %H:%M" if updated.year == datetime.now().astimezone().year else "%Y-%m-%d %H:%M"
    return updated.strftime(pattern)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class SessionBrowser:
    async def _session_page(self, *, cursor=None, archived=False) -> CodexThreadPage:
        """Fill a visible page without dropping matches at upstream boundaries.

        Codex's cwd filter is exact-match only. Filter descendants locally, even
        for removed worktrees, and request only the remaining number of rows.
        """
        threads = []
        seen = set()
        while True:
            if cursor in seen:
                raise RuntimeError("Codex 返回了重复的分页游标，请刷新列表。")
            seen.add(cursor)
            remaining = self.config.page_size - len(threads)
            page = await asyncio.to_thread(
                self.catalog.list_threads, cursor=cursor,
                limit=remaining or self.config.page_size, archived=archived,
            )
            matches = [item for item in page.threads if self._cwd_allowed(item.cwd)]
            if not remaining and matches:
                # Look ahead without consuming matches: do not offer an empty
                # Next page when only unrelated projects remain upstream.
                return CodexThreadPage(threads, next_cursor=cursor)
            threads.extend(matches)
            cursor = page.next_cursor
            if not cursor:
                return CodexThreadPage(threads)

    def _session_button(self, kind, label, channel_id, payload, *, confirm=None) -> dict:
        token = self._action_token(kind, channel_id=channel_id, payload=payload)
        button = {
            "type": "button", "text": {"type": "plain_text", "text": label},
            # Distinct row tokens keep same-suffix sessions unambiguous.
            "action_id": f"codex_slack_{kind}:{token}", "value": token,
        }
        if confirm:
            button["confirm"] = confirm
        return button

    async def show_sessions(self, channel_id, user_id, *, cursor=None, history=None,
                            archived=False, notice="") -> None:
        history = list(history or [])
        try:
            page = await self._session_page(cursor=cursor, archived=archived)
        except Exception as exc:
            await self._post_ephemeral(channel_id, user_id, f"无法列出 Codex sessions：{exc}")
            return
        heading = "已归档的 Codex sessions" if archived else "最近的 Codex sessions"
        heading += f"（每页 {self.config.page_size} 个）"
        blocks = [{"type": "section", "text": {"type": "plain_text", "text": heading}}]
        if notice:
            blocks.append({"type": "context", "elements": [{"type": "plain_text", "text": notice}]})
        blocks.append({"type": "context", "elements": [{"type": "plain_text", "text":
            f"{'已归档' if archived else '未归档'} · 第 {len(history) + 1} 页 · 点击标题 Resume"}]})
        for item in page.threads:
            blocks.append(self._session_row(item, channel_id, archived=archived))
        if not page.threads:
            blocks.append({"type": "section", "text": {"type": "plain_text", "text": "该范围内没有找到会话。"}})
        navigation = []
        if history:
            navigation.append(self._session_button("page", "Previous", channel_id, {
                "cursor": history[-1], "history": history[:-1], "archived": archived}))
        if page.next_cursor:
            navigation.append(self._session_button("page", "Next", channel_id, {
                "cursor": page.next_cursor, "history": [*history, cursor], "archived": archived}))
        navigation.append(self._session_button("page", "未归档" if archived else "已归档", channel_id,
                                               {"archived": not archived}))
        blocks.append({"type": "actions", "elements": navigation})
        await self._post_ephemeral(channel_id, user_id, heading, blocks=blocks)
        self._discard_slash_context(channel_id)

    def _session_row(self, item, channel_id, *, archived=False) -> dict:
        # Put the title and date inside Resume so both controls share a row.
        payload = {"codex_thread_id": item.thread_id, "archived": archived}
        actions = []
        for kind, label in (("resume", "Resume"), ("archive", "Archive")):
            if kind == "archive" and archived:
                continue
            confirm = None
            if kind == "archive":
                confirm = {
                    "title": {"type": "plain_text", "text": "归档会话？"},
                    "text": {"type": "plain_text", "text":
                        f"{item.thread_id[-4:]} · {_clip(item.title, 90)}\n"
                        "将归档该会话及其派生子会话的记录。可从“已归档”列表 Resume 恢复。"},
                    "confirm": {"type": "plain_text", "text": label},
                    "deny": {"type": "plain_text", "text": "取消"},
                }
            if kind == "resume":
                updated = _date(item.updated_at)
                # Slack can truncate button captions around 30 characters.
                # Reserve space for the whole timestamp before clipping titles.
                label = f"{_clip(item.title, 30 - len(updated) - 3)} · {updated}"
            button = self._session_button(kind, label, channel_id, payload, confirm=confirm)
            if kind == "resume":
                button["accessibility_label"] = _clip(f"Resume {item.title}", 75)
            actions.append(button)
        return {"type": "actions", "elements": actions}

    async def archive_session(self, thread_id, *, channel_id, user_id, archived=False) -> None:
        """Only explicit list actions may archive; never interrupt work."""
        if thread_id in self._managing_sessions:
            await self._post_ephemeral(channel_id, user_id, "该会话正在处理操作，请稍后重试。")
            return
        self._managing_sessions.add(thread_id)
        try:
            lock = self._mirror_locks.setdefault(thread_id, asyncio.Lock())
            async with lock:
                runtime = self._runtimes.get(thread_id)
                if runtime and (runtime.active or runtime.queue):
                    raise ValueError("该会话正在运行，请先在原 thread 中 !stop，再执行此操作。")
                summary = await asyncio.to_thread(self.catalog.read_thread, thread_id)
                if not self._cwd_allowed(summary.cwd):
                    raise ValueError("该会话不在允许的项目路径范围内。")
                snapshot = await asyncio.to_thread(self.catalog.read_activity, thread_id)
                status = snapshot.get("status") or {}
                if (status.get("type") if isinstance(status, dict) else status) == "active" or any(
                    turn.get("status") == "inProgress" for turn in snapshot.get("turns", [])
                ):
                    raise ValueError("该会话正在运行，请先停止运行，再执行此操作。")
                await asyncio.to_thread(self.catalog.archive_thread, thread_id)
                binding = self.store.by_codex(thread_id)
                if binding:
                    self.store.set_state(binding, "archived")
                if runtime:
                    await self._close_runtime(runtime)
                    self._runtimes.pop(thread_id, None)
                with self._action_lock:
                    for token, state in list(self._actions.items()):
                        if state.payload.get("codex_thread_id") == thread_id:
                            self._actions.pop(token, None)
        except Exception as exc:
            await self._post_ephemeral(channel_id, user_id, f"操作未完成：{exc}")
            return
        finally:
            self._managing_sessions.discard(thread_id)
        await self.show_sessions(channel_id, user_id, archived=archived, notice=f"{thread_id[-4:]} 已归档。")
