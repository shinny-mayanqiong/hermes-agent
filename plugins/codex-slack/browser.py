"""Project-scoped session cards and explicit Codex lifecycle actions."""

from __future__ import annotations

import asyncio
from datetime import datetime

from agent.transports.codex_app_server_session import CodexThreadPage

ACTION_PAGE = "codex_slack_page"
ACTION_RESUME = "codex_slack_resume"
ACTION_ARCHIVE = "codex_slack_archive"
ACTION_DELETE = "codex_slack_delete"


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S") if timestamp else "—"


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
        if kind == "delete":
            button["style"] = "danger"
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
            f"{'已归档' if archived else '未归档'} · 第 {len(history) + 1} 页 · "
            f"时间 {datetime.now().astimezone().strftime('%Z %z')} · 路径范围："
            + "、".join(self.config.allowed_roots)}]})
        cards = [self._session_card(item, channel_id, archived=archived) for item in page.threads]
        if not cards:
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
        # Keep each card and its buttons together. An unusually long path can
        # require extra sections; split the logical page into messages rather
        # than truncate data or exceed Slack's 50-block message limit.
        batches = [[]]
        for card in cards:
            if len(blocks) + len(batches[-1]) + len(card) + 1 > 50:
                batches.append([])
            batches[-1].extend(card)
        for index, batch in enumerate(batches):
            footer = ({"type": "actions", "elements": navigation}
                      if index == len(batches) - 1 else
                      {"type": "context", "elements": [{"type": "plain_text", "text": "本页其余会话见下一条消息。"}]})
            await self._post_ephemeral(channel_id, user_id, heading, blocks=[*blocks, *batch, footer])
        self._discard_slash_context(channel_id)

    def _session_card(self, item, channel_id, *, archived=False) -> list[dict]:
        text = (f"{item.thread_id[-4:]} · {_clip(item.title, 160)}\n"
                f"创建：{_date(item.created_at)}\n"
                f"更新：{_date(item.updated_at)}\n"
                f"路径：{item.cwd or '—'}")
        # Single-column text wraps naturally on narrow Slack clients. Preserve
        # full timestamps and paths, including spaces and long worktree names.
        blocks = [{"type": "section", "expand": True,
                   "text": {"type": "plain_text", "emoji": False, "text": text[offset:offset + 3000]}}
                  for offset in range(0, len(text), 3000)]
        payload = {"codex_thread_id": item.thread_id, "archived": archived}
        actions = []
        for kind, label in (("resume", "Resume"), ("archive", "Archive"), ("delete", "Delete")):
            if kind == "archive" and archived:
                continue
            confirm = None
            if kind in {"archive", "delete"}:
                verb = "永久删除" if kind == "delete" else "归档"
                consequence = ("此操作不可恢复；仅删除会话记录，不删除项目文件。"
                               if kind == "delete" else "可从“已归档”列表 Resume 恢复。")
                confirm = {
                    "title": {"type": "plain_text", "text": f"{verb}会话？"},
                    "text": {"type": "plain_text", "text":
                        f"{item.thread_id[-4:]} · {_clip(item.title, 90)}\n"
                        f"将{verb}该会话及其派生子会话的记录。{consequence}"},
                    "confirm": {"type": "plain_text", "text": label},
                    "deny": {"type": "plain_text", "text": "取消"},
                }
            actions.append(self._session_button(kind, label, channel_id, payload, confirm=confirm))
        blocks.append({"type": "actions", "elements": actions})
        return blocks

    async def manage_session(self, kind, thread_id, *, channel_id, user_id, archived=False) -> None:
        """Only explicit card actions may archive/delete; never interrupt work."""
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
                operation = self.catalog.archive_thread if kind == "archive" else self.catalog.delete_thread
                await asyncio.to_thread(operation, thread_id)
                binding = self.store.by_codex(thread_id)
                if binding:
                    self.store.set_state(binding, "archived" if kind == "archive" else "deleted")
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
        verb = "已归档" if kind == "archive" else "已永久删除"
        await self.show_sessions(channel_id, user_id, archived=archived, notice=f"{thread_id[-4:]} {verb}。")
