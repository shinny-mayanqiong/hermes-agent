"""Slack control surface for local Codex app-server threads."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from agent.transports.codex_app_server_session import (
    CodexAppServerSession,
    CodexThreadCatalog,
    CodexThreadPage,
    CodexThreadSummary,
)
from gateway.config import Platform
from hermes_constants import get_hermes_home
from hermes_cli.config import load_config

from .store import Binding, BindingConflict, BindingStore  # type: ignore[unresolved-import]

logger = logging.getLogger(__name__)

COMMAND_NAME = "codex"
COMMAND_PREFIX = "/codex"

ACTION_PAGE = "codex_slack_page"
ACTION_RESUME = "codex_slack_resume"
ACTION_OPEN = "codex_slack_open"
ACTION_APPROVE_ONCE = "codex_slack_approve_once"
ACTION_APPROVE_SESSION = "codex_slack_approve_session"
ACTION_DENY = "codex_slack_deny"


@dataclass(frozen=True)
class PluginConfig:
    default_cwd: str = "/home/user/Repos/odoo-hedge"
    allowed_roots: tuple[str, ...] = ("/home/user/Repos",)
    page_size: int = 20
    queue_limit: int = 5
    max_live_sessions: int = 5
    idle_timeout_seconds: int = 1800
    approval_timeout_seconds: int = 600


@dataclass
class _ActionState:
    kind: str
    channel_id: str
    payload: dict[str, Any]
    expires_at: float


@dataclass
class _PendingApproval:
    binding: Binding
    command: str
    description: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: str = "deny"
    message_ts: str = ""


@dataclass
class _QueuedTurn:
    text: str
    user_id: str


@dataclass
class _Runtime:
    binding: Binding
    queue: deque[_QueuedTurn] = field(default_factory=deque)
    session: Optional[CodexAppServerSession] = None
    worker: Optional[asyncio.Task] = None
    active: bool = False
    stopping: bool = False
    last_active: float = field(default_factory=time.monotonic)
    progress_ts: str = ""
    progress_updated_at: float = 0.0


def _load_plugin_config() -> PluginConfig:
    try:
        config = load_config() or {}
    except Exception:
        logger.warning("[codex-slack] Failed to load config.yaml", exc_info=True)
        config = {}
    plugins = config.get("plugins") if isinstance(config, dict) else {}
    raw = plugins.get("codex-slack", {}) if isinstance(plugins, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    default_cwd = str(raw.get("default_cwd") or PluginConfig.default_cwd)
    roots = raw.get("allowed_roots") or list(PluginConfig.allowed_roots)
    if isinstance(roots, str):
        roots = [roots]
    normalized_roots = tuple(str(Path(os.path.expanduser(str(root))).resolve()) for root in roots)

    def positive_int(name: str, default: int, *, maximum: Optional[int] = None) -> int:
        try:
            value = max(1, int(raw.get(name, default)))
        except (TypeError, ValueError):
            value = default
        return min(value, maximum) if maximum is not None else value

    return PluginConfig(
        default_cwd=str(Path(os.path.expanduser(default_cwd)).resolve()),
        allowed_roots=normalized_roots,
        page_size=positive_int("page_size", 20, maximum=20),
        queue_limit=positive_int("queue_limit", 5),
        max_live_sessions=positive_int("max_live_sessions", 5),
        idle_timeout_seconds=positive_int("idle_timeout_seconds", 1800),
        approval_timeout_seconds=positive_int("approval_timeout_seconds", 600),
    )


def _platform_value(source: Any) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "").lower()


def _raw_message(event: Any) -> dict[str, Any]:
    raw = getattr(event, "raw_message", None)
    return raw if isinstance(raw, dict) else {}


def _team_id(event: Any) -> str:
    source = getattr(event, "source", None)
    raw = _raw_message(event)
    team = raw.get("team_id") or raw.get("team") or getattr(source, "guild_id", None)
    if isinstance(team, dict):
        team = team.get("id")
    return str(team or "")


def _command_args(text: str) -> Optional[str]:
    stripped = str(text or "").strip()
    lowered = stripped.lower()
    if lowered == COMMAND_PREFIX or lowered == "!codex":
        return ""
    for prefix in (COMMAND_PREFIX + " ", "!codex "):
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def _event_command_args(event: Any) -> Optional[str]:
    """Accept native /codex and the already-installed /hermes fallback."""
    text = str(getattr(event, "text", "") or "")
    direct = _command_args(text)
    if direct is not None:
        return direct
    raw = _raw_message(event)
    if str(raw.get("command") or "").strip().lower() != "/hermes":
        return None
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered == "codex":
        return ""
    if lowered.startswith("codex "):
        return stripped[len("codex ") :].strip()
    return None


def _slack_data(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    getter = getattr(response, "get", None)
    if not callable(getter):
        return {}
    return {key: getter(key) for key in ("ok", "ts", "message_ts", "permalink", "error")}


def _friendly_codex_error(exc: Exception) -> str:
    message = str(exc)
    if "already has an active writer" in message.lower():
        return "该 Codex session 正被另一个本机 Codex 客户端使用；请先结束那边的运行后重试。"
    return f"Codex 操作失败：{message}"


class CodexSlackManager:
    def __init__(
        self,
        *,
        config: Optional[PluginConfig] = None,
        store: Optional[BindingStore] = None,
        catalog: Optional[CodexThreadCatalog] = None,
        session_factory: Callable[..., CodexAppServerSession] = CodexAppServerSession,
    ) -> None:
        self.config = config or _load_plugin_config()
        self.store = store or BindingStore(
            get_hermes_home() / "state" / "codex-slack.sqlite3"
        )
        self.catalog = catalog or CodexThreadCatalog()
        self.session_factory = session_factory
        self.gateway: Any = None
        self._runtimes: OrderedDict[str, _Runtime] = OrderedDict()
        self._actions: dict[str, _ActionState] = {}
        self._approvals: dict[str, _PendingApproval] = {}
        self._action_lock = threading.RLock()
        self._reaper: Optional[asyncio.Task] = None

    # ---------- gateway + Slack helpers ----------

    def _home_channel(self, gateway: Any = None) -> str:
        gateway = gateway or self.gateway
        if gateway is None:
            return ""
        try:
            home = gateway.config.get_home_channel(Platform.SLACK)
        except Exception:
            return ""
        return str(getattr(home, "chat_id", "") or "") if home else ""

    def _adapter(self, gateway: Any = None) -> Any:
        gateway = gateway or self.gateway
        return gateway.adapters.get(Platform.SLACK) if gateway is not None else None

    @staticmethod
    def _client(adapter: Any, channel_id: str) -> Any:
        getter = getattr(adapter, "_get_client", None)
        if callable(getter):
            return getter(channel_id)
        return getattr(getattr(adapter, "_app", None), "client", None)

    async def _post_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str = "",
        blocks: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        adapter = self._adapter()
        client = self._client(adapter, channel_id)
        if client is None:
            raise RuntimeError("Slack client is not available")
        formatter = getattr(adapter, "format_message", None)
        rendered = formatter(text) if callable(formatter) else text
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": rendered,
            "mrkdwn": True,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if blocks:
            kwargs["blocks"] = blocks
        response = _slack_data(await client.chat_postMessage(**kwargs))
        sent_ts = str(response.get("ts") or "")
        bot_messages = getattr(adapter, "_bot_message_ts", None)
        if hasattr(bot_messages, "add"):
            if thread_ts:
                bot_messages.add(thread_ts)
            if sent_ts:
                bot_messages.add(sent_ts)
        return response

    async def _post_ephemeral(
        self,
        channel_id: str,
        user_id: str,
        text: str,
        *,
        blocks: Optional[list[dict[str, Any]]] = None,
        thread_ts: str = "",
    ) -> dict[str, Any]:
        adapter = self._adapter()
        client = self._client(adapter, channel_id)
        if client is None:
            raise RuntimeError("Slack client is not available")
        formatter = getattr(adapter, "format_message", None)
        rendered = formatter(text) if callable(formatter) else text
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "user": user_id,
            "text": rendered,
            "mrkdwn": True,
        }
        if blocks:
            kwargs["blocks"] = blocks
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        return _slack_data(await client.chat_postEphemeral(**kwargs))

    async def _edit_message(self, channel_id: str, message_ts: str, text: str) -> None:
        if not message_ts:
            return
        adapter = self._adapter()
        client = self._client(adapter, channel_id)
        if client is None:
            return
        formatter = getattr(adapter, "format_message", None)
        rendered = formatter(text) if callable(formatter) else text
        try:
            await client.chat_update(channel=channel_id, ts=message_ts, text=rendered)
        except Exception:
            logger.debug("[codex-slack] Slack progress update failed", exc_info=True)

    def _discard_slash_context(self, channel_id: str) -> None:
        adapter = self._adapter()
        pop_context = getattr(adapter, "_pop_slash_context", None)
        if callable(pop_context):
            try:
                pop_context(channel_id)
            except Exception:
                logger.debug("[codex-slack] Failed to discard slash context", exc_info=True)

    async def _permalink(self, binding: Binding) -> Optional[str]:
        client = self._client(self._adapter(), binding.channel_id)
        if client is None or not hasattr(client, "chat_getPermalink"):
            return f"Slack thread {binding.thread_ts}"
        try:
            response = _slack_data(
                await client.chat_getPermalink(
                    channel=binding.channel_id,
                    message_ts=binding.thread_ts,
                )
            )
            return str(response.get("permalink") or "") or None
        except Exception as exc:
            error = str(getattr(getattr(exc, "response", None), "get", lambda *_: "")("error") or exc)
            if "message_not_found" in error or "channel_not_found" in error:
                self.store.delete(binding)
                return None
            raise

    # ---------- validation + binding ----------

    def resolve_cwd(self, raw: str = "") -> str:
        value = str(raw or "").strip()
        if not value:
            candidate = Path(self.config.default_cwd)
        else:
            expanded = Path(os.path.expanduser(value))
            candidate = (
                expanded
                if expanded.is_absolute()
                else Path(self.config.allowed_roots[0]) / expanded
            )
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(f"目录不存在：{candidate}") from exc
        if not resolved.is_dir():
            raise ValueError(f"不是目录：{resolved}")
        if not self._cwd_allowed(str(resolved)):
            raise ValueError(
                f"目录必须位于允许范围内：{', '.join(self.config.allowed_roots)}"
            )
        return str(resolved)

    def _cwd_allowed(self, cwd: str) -> bool:
        try:
            resolved = Path(os.path.expanduser(str(cwd))).resolve(strict=True)
        except (FileNotFoundError, OSError):
            return False
        for root in self.config.allowed_roots:
            try:
                resolved.relative_to(Path(root).resolve(strict=True))
                return True
            except (ValueError, FileNotFoundError, OSError):
                continue
        return False

    async def _bind_thread(
        self,
        *,
        summary: CodexThreadSummary,
        team_id: str,
        channel_id: str,
        user_id: str,
        resumed: bool,
    ) -> Binding:
        existing = self.store.by_codex(summary.thread_id)
        if existing:
            link = await self._permalink(existing)
            if link:
                await self._post_ephemeral(
                    channel_id,
                    user_id,
                    f"该 Codex session 已绑定：<{link}|打开原 thread>",
                )
                return existing

        # Prove that the app-server can start/resume before publishing a root.
        session = self.session_factory(
            cwd=summary.cwd,
            resume_thread_id=summary.thread_id if resumed else None,
        )
        try:
            thread_id = await asyncio.to_thread(session.ensure_started)
        finally:
            await asyncio.to_thread(session.close)
        summary = CodexThreadSummary(
            thread_id=thread_id,
            title=summary.title,
            cwd=summary.cwd,
            source_kind=summary.source_kind,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
            recency_at=summary.recency_at,
        )

        root_text = "\n".join(
            [
                f"*Codex session {'已恢复' if resumed else '已创建'}*",
                f"ID: `{summary.thread_id}`",
                f"目录: `{summary.cwd}`",
                "直接在此 thread 回复即可继续；`!status` 查看状态，`!stop` 停止并释放进程。",
            ]
        )
        response = await self._post_message(channel_id, root_text)
        thread_ts = str(response.get("ts") or "")
        if not thread_ts:
            raise RuntimeError("Slack did not return a root message timestamp")
        now = time.time()
        binding = Binding(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            codex_thread_id=summary.thread_id,
            cwd=summary.cwd,
            created_at=now,
            last_used_at=now,
        )
        try:
            self.store.create(binding)
        except BindingConflict:
            winner = self.store.by_codex(summary.thread_id)
            if winner:
                await self._post_message(
                    channel_id,
                    "绑定发生并发冲突；请使用 `/codex sessions` 打开已经存在的 thread。",
                    thread_ts=thread_ts,
                )
                return winner
            raise
        self._discard_slash_context(channel_id)
        await self._post_ephemeral(
            channel_id,
            user_id,
            f"已{'恢复' if resumed else '创建'}，请在新 thread 中继续。",
        )
        return binding

    # ---------- command + session picker ----------

    @staticmethod
    def usage() -> str:
        return (
            "`/codex sessions` 查看本机 session\n"
            "`/codex new [目录]` 创建 session\n"
            "`/codex resume <session-id> [新目录]` 恢复 session"
        )

    async def handle_command(self, event: Any) -> None:
        source = getattr(event, "source", None)
        channel_id = str(getattr(source, "chat_id", "") or "")
        user_id = str(getattr(source, "user_id", "") or "")
        # We reply through Slack's private-message API directly, so consume
        # the adapter's native-slash response context before a later unrelated
        # adapter.send() can accidentally replace this command's ACK.
        self._discard_slash_context(channel_id)
        home_channel = self._home_channel()
        if not home_channel:
            await self._post_ephemeral(
                channel_id,
                user_id,
                "尚未配置 Slack home channel；请先在目标频道执行 `/sethome`。",
            )
            return
        if channel_id != home_channel:
            await self._post_ephemeral(
                channel_id,
                user_id,
                "`/codex` 只能在 gateway 启动通知使用的 Slack home channel 中运行。",
            )
            return

        args = _event_command_args(event)
        try:
            argv = shlex.split(args or "")
        except ValueError as exc:
            await self._post_ephemeral(channel_id, user_id, f"命令参数无法解析：{exc}")
            return
        if not argv or argv[0].lower() in {"help", "帮助"}:
            await self._post_ephemeral(channel_id, user_id, self.usage())
            return
        command = argv[0].lower()
        if command == "sessions":
            await self.show_sessions(channel_id, user_id)
            return
        if command == "new":
            raw_cwd = " ".join(argv[1:])
            try:
                cwd = self.resolve_cwd(raw_cwd)
            except ValueError as exc:
                await self._post_ephemeral(channel_id, user_id, str(exc))
                return
            summary = CodexThreadSummary(
                thread_id="",
                title="New Codex session",
                cwd=cwd,
                source_kind="appServer",
                created_at=int(time.time()),
                updated_at=int(time.time()),
                recency_at=int(time.time()),
            )
            try:
                await self._bind_thread(
                    summary=summary,
                    team_id=_team_id(event),
                    channel_id=channel_id,
                    user_id=user_id,
                    resumed=False,
                )
            except Exception as exc:
                logger.warning("[codex-slack] Failed to create Codex session: %s", exc)
                await self._post_ephemeral(channel_id, user_id, _friendly_codex_error(exc))
            return
        if command == "resume" and len(argv) >= 2:
            await self.resume_by_id(
                argv[1],
                team_id=_team_id(event),
                channel_id=channel_id,
                user_id=user_id,
                cwd_override=" ".join(argv[2:]) or None,
            )
            return
        await self._post_ephemeral(channel_id, user_id, self.usage())

    async def resume_by_id(
        self,
        thread_id: str,
        *,
        team_id: str,
        channel_id: str,
        user_id: str,
        cwd_override: Optional[str] = None,
    ) -> None:
        try:
            summary = await asyncio.to_thread(self.catalog.read_thread, thread_id)
        except Exception as exc:
            await self._post_ephemeral(channel_id, user_id, f"无法读取 Codex session：{exc}")
            return
        requested_cwd = cwd_override if cwd_override is not None else summary.cwd
        try:
            cwd = self.resolve_cwd(requested_cwd)
        except ValueError as exc:
            await self._post_ephemeral(
                channel_id,
                user_id,
                f"无法恢复该 session：{exc}",
            )
            return
        summary = replace(summary, cwd=cwd)
        try:
            await self._bind_thread(
                summary=summary,
                team_id=team_id,
                channel_id=channel_id,
                user_id=user_id,
                resumed=True,
            )
        except Exception as exc:
            logger.warning("[codex-slack] Failed to resume Codex session: %s", exc)
            await self._post_ephemeral(channel_id, user_id, _friendly_codex_error(exc))

    def _action_token(
        self,
        kind: str,
        *,
        channel_id: str,
        payload: dict[str, Any],
    ) -> str:
        token = secrets.token_urlsafe(12)
        with self._action_lock:
            self._actions[token] = _ActionState(
                kind=kind,
                channel_id=channel_id,
                payload=payload,
                expires_at=time.time() + 600,
            )
        return token

    def _take_action(self, token: str, *, channel_id: str) -> Optional[_ActionState]:
        with self._action_lock:
            now = time.time()
            for key in [key for key, state in self._actions.items() if state.expires_at <= now]:
                self._actions.pop(key, None)
            state = self._actions.pop(token, None)
        if not state or state.expires_at <= now:
            return None
        if state.channel_id != channel_id:
            return None
        return state

    async def show_sessions(
        self,
        channel_id: str,
        user_id: str,
        *,
        cursor: Optional[str] = None,
        history: Optional[list[Optional[str]]] = None,
    ) -> None:
        history = list(history or [])
        try:
            page: CodexThreadPage = await asyncio.to_thread(
                self.catalog.list_threads,
                cursor=cursor,
                limit=self.config.page_size,
            )
        except Exception as exc:
            await self._post_ephemeral(channel_id, user_id, f"无法列出 Codex sessions：{exc}")
            return
        bound = self.store.by_codex_many(item.thread_id for item in page.threads)
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*最近的 Codex sessions*（每页 {self.config.page_size} 个）"},
            }
        ]
        if not page.threads:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "没有找到 session。"}})
        for item in page.threads:
            timestamp = datetime.fromtimestamp(item.recency_at or item.updated_at).strftime("%Y-%m-%d %H:%M")
            title = item.title.replace("\n", " ")[:90]
            text = f"*{title}*\n`{item.thread_id}` · {timestamp}\n`{item.cwd}`"
            block: dict[str, Any] = {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
            binding = bound.get(item.thread_id)
            if binding:
                token = self._action_token(
                    "open",
                    channel_id=channel_id,
                    payload={"codex_thread_id": item.thread_id},
                )
                block["accessory"] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open thread"},
                    "action_id": ACTION_OPEN,
                    "value": token,
                }
            else:
                try:
                    self.resolve_cwd(item.cwd)
                except ValueError as exc:
                    block["text"]["text"] += (
                        f"\n_{exc}；可用 `/codex resume {item.thread_id} <新目录>` 恢复_"
                    )
                    blocks.append(block)
                    continue
                token = self._action_token(
                    "resume",
                    channel_id=channel_id,
                    payload={"codex_thread_id": item.thread_id},
                )
                block["accessory"] = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Resume"},
                    "action_id": ACTION_RESUME,
                    "value": token,
                }
            blocks.append(block)

        navigation: list[dict[str, Any]] = []
        if history:
            previous_cursor = history[-1]
            token = self._action_token(
                "page",
                channel_id=channel_id,
                payload={"cursor": previous_cursor, "history": history[:-1]},
            )
            navigation.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Previous"},
                "action_id": ACTION_PAGE,
                "value": token,
            })
        if page.next_cursor:
            token = self._action_token(
                "page",
                channel_id=channel_id,
                payload={"cursor": page.next_cursor, "history": [*history, cursor]},
            )
            navigation.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Next"},
                "action_id": ACTION_PAGE,
                "value": token,
            })
        if navigation:
            blocks.append({"type": "actions", "elements": navigation})
        await self._post_ephemeral(
            channel_id,
            user_id,
            f"最近的 Codex sessions（每页 {self.config.page_size} 个）",
            blocks=blocks,
        )
        self._discard_slash_context(channel_id)

    # ---------- bound-thread runtime ----------

    def _live_runtime_count(self) -> int:
        return sum(1 for runtime in self._runtimes.values() if runtime.session is not None)

    async def _close_runtime(self, runtime: _Runtime) -> None:
        session, runtime.session = runtime.session, None
        if session is not None:
            await asyncio.to_thread(session.close)

    async def _prune_runtimes(self, *, reserve_slot: bool = False) -> None:
        now = time.monotonic()
        for key, runtime in list(self._runtimes.items()):
            if not runtime.active and now - runtime.last_active >= self.config.idle_timeout_seconds:
                await self._close_runtime(runtime)
                self._runtimes.pop(key, None)
        def over_limit() -> bool:
            live = self._live_runtime_count()
            return (
                live >= self.config.max_live_sessions
                if reserve_slot
                else live > self.config.max_live_sessions
            )

        while over_limit():
            victim_key = next(
                (key for key, runtime in self._runtimes.items() if not runtime.active and runtime.session is not None),
                None,
            )
            if victim_key is None:
                break
            victim = self._runtimes.pop(victim_key)
            await self._close_runtime(victim)

    def _ensure_reaper(self) -> None:
        if self._reaper is not None and not self._reaper.done():
            return

        async def reaper() -> None:
            while True:
                await asyncio.sleep(min(60, self.config.idle_timeout_seconds))
                await self._prune_runtimes()

        self._reaper = asyncio.create_task(reaper())

    def _runtime(self, binding: Binding) -> _Runtime:
        runtime = self._runtimes.get(binding.codex_thread_id)
        if runtime is None:
            runtime = _Runtime(binding=binding)
            self._runtimes[binding.codex_thread_id] = runtime
        else:
            self._runtimes.move_to_end(binding.codex_thread_id)
        runtime.last_active = time.monotonic()
        return runtime

    async def enqueue_turn(self, binding: Binding, text: str, user_id: str) -> None:
        self._ensure_reaper()
        runtime = self._runtime(binding)
        waiting = len(runtime.queue)
        if runtime.active and waiting >= self.config.queue_limit:
            await self._post_message(
                binding.channel_id,
                f"队列已满（最多等待 {self.config.queue_limit} 条），请稍后重试。",
                thread_ts=binding.thread_ts,
            )
            return
        runtime.queue.append(_QueuedTurn(text=text, user_id=user_id))
        if runtime.active:
            await self._post_message(
                binding.channel_id,
                f"已排队，当前位置：{len(runtime.queue)}。",
                thread_ts=binding.thread_ts,
            )
            return
        runtime.worker = asyncio.create_task(self._run_queue(runtime))

    async def _ensure_session(self, runtime: _Runtime) -> bool:
        if runtime.session is not None:
            return True
        await self._prune_runtimes(reserve_slot=True)
        if self._live_runtime_count() >= self.config.max_live_sessions:
            await self._post_message(
                runtime.binding.channel_id,
                f"当前已有 {self.config.max_live_sessions} 个活动 Codex session，请先 `!stop` 一个。",
                thread_ts=runtime.binding.thread_ts,
            )
            return False
        loop = asyncio.get_running_loop()
        runtime.session = self.session_factory(
            cwd=runtime.binding.cwd,
            resume_thread_id=runtime.binding.codex_thread_id,
            approval_callback=self._approval_callback(runtime.binding, loop),
            on_event=lambda note: self._on_codex_event(runtime, loop, note),
        )
        return True

    async def _run_queue(self, runtime: _Runtime) -> None:
        runtime.active = True
        try:
            while runtime.queue and not runtime.stopping:
                item = runtime.queue.popleft()
                if not await self._ensure_session(runtime):
                    runtime.queue.clear()
                    break
                response = await self._post_message(
                    runtime.binding.channel_id,
                    "⏳ Codex 正在处理…",
                    thread_ts=runtime.binding.thread_ts,
                )
                runtime.progress_ts = str(response.get("ts") or "")
                runtime.progress_updated_at = 0.0
                session = runtime.session
                assert session is not None
                result = await asyncio.to_thread(session.run_turn, item.text)
                runtime.last_active = time.monotonic()
                self.store.touch(runtime.binding)
                if runtime.stopping:
                    break
                if result.error:
                    await self._edit_message(
                        runtime.binding.channel_id,
                        runtime.progress_ts,
                        "⚠️ Codex turn 未完成。",
                    )
                    await self._post_message(
                        runtime.binding.channel_id,
                        result.error,
                        thread_ts=runtime.binding.thread_ts,
                    )
                else:
                    await self._edit_message(
                        runtime.binding.channel_id,
                        runtime.progress_ts,
                        "✅ Codex 已完成。",
                    )
                    await self._send_final(runtime.binding, result.final_text or "Codex 已完成，但没有返回文本。")
                if result.should_retire:
                    await self._close_runtime(runtime)
        except Exception as exc:
            logger.exception("[codex-slack] Codex worker failed")
            await self._post_message(
                runtime.binding.channel_id,
                f"Codex 运行失败：{exc}",
                thread_ts=runtime.binding.thread_ts,
            )
            await self._close_runtime(runtime)
        finally:
            runtime.active = False
            runtime.worker = None
            runtime.last_active = time.monotonic()
            if runtime.stopping:
                await self._close_runtime(runtime)
                self._runtimes.pop(runtime.binding.codex_thread_id, None)

    async def _send_final(self, binding: Binding, text: str) -> None:
        adapter = self._adapter()
        sender = getattr(adapter, "send", None)
        if callable(sender):
            result = sender(
                binding.channel_id,
                text,
                metadata={"thread_id": binding.thread_ts, "thread_ts": binding.thread_ts},
            )
            if asyncio.iscoroutine(result):
                await result
            return
        await self._post_message(binding.channel_id, text, thread_ts=binding.thread_ts)

    def _on_codex_event(self, runtime: _Runtime, loop: asyncio.AbstractEventLoop, note: dict) -> None:
        method = str(note.get("method") or "")
        if method not in {"item/started", "item/completed"}:
            return
        item = (note.get("params") or {}).get("item") or {}
        item_type = str(item.get("type") or "work")
        state = "运行" if method.endswith("started") else "完成"
        label = {
            "commandExecution": "命令",
            "fileChange": "文件修改",
            "mcpToolCall": "工具调用",
        }.get(item_type, "步骤")

        def schedule() -> None:
            asyncio.create_task(self._update_progress(runtime, f"⏳ {label}{state}…"))

        loop.call_soon_threadsafe(schedule)

    async def _update_progress(self, runtime: _Runtime, text: str) -> None:
        now = time.monotonic()
        if now - runtime.progress_updated_at < 1.0:
            return
        runtime.progress_updated_at = now
        await self._edit_message(runtime.binding.channel_id, runtime.progress_ts, text)

    async def status(self, binding: Binding) -> None:
        runtime = self._runtimes.get(binding.codex_thread_id)
        if runtime and runtime.active:
            state = "运行中"
        elif runtime and runtime.session is not None:
            state = "空闲（进程已缓存）"
        else:
            state = "休眠（无运行进程）"
        queued = len(runtime.queue) if runtime else 0
        await self._post_message(
            binding.channel_id,
            f"状态：*{state}*\nCodex ID：`{binding.codex_thread_id}`\n目录：`{binding.cwd}`\n排队：{queued}",
            thread_ts=binding.thread_ts,
        )

    async def stop(self, binding: Binding) -> None:
        runtime = self._runtimes.get(binding.codex_thread_id)
        if runtime is None:
            await self._post_message(
                binding.channel_id,
                "已停止并释放资源；下次消息将自动恢复原 Codex session。",
                thread_ts=binding.thread_ts,
            )
            return
        runtime.stopping = True
        dropped = len(runtime.queue)
        runtime.queue.clear()
        self._deny_pending_approvals(binding)
        if runtime.session is not None:
            runtime.session.request_interrupt()
        worker = runtime.worker
        if worker is not None and worker is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=10)
            except asyncio.TimeoutError:
                await self._close_runtime(runtime)
                self._runtimes.pop(binding.codex_thread_id, None)
        else:
            await self._close_runtime(runtime)
            self._runtimes.pop(binding.codex_thread_id, None)
        suffix = f"，并清空 {dropped} 条排队消息" if dropped else ""
        await self._post_message(
            binding.channel_id,
            f"已停止并释放资源{suffix}；下次消息将自动恢复原 Codex session。",
            thread_ts=binding.thread_ts,
        )

    def _deny_pending_approvals(self, binding: Binding) -> None:
        with self._action_lock:
            pending = [
                approval
                for approval in self._approvals.values()
                if approval.binding.codex_thread_id == binding.codex_thread_id
            ]
        for approval in pending:
            approval.decision = "deny"
            approval.event.set()

    # ---------- Slack approval bridge ----------

    def _approval_callback(
        self,
        binding: Binding,
        loop: asyncio.AbstractEventLoop,
    ) -> Callable[..., str]:
        def callback(command: str, description: str, allow_permanent: bool = False) -> str:
            del allow_permanent
            token = secrets.token_urlsafe(12)
            pending = _PendingApproval(
                binding=binding,
                command=str(command),
                description=str(description),
            )
            with self._action_lock:
                self._approvals[token] = pending
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._post_approval(token, pending), loop
                )
                future.result(timeout=10)
                if not pending.event.wait(timeout=self.config.approval_timeout_seconds):
                    return "deny"
                return pending.decision
            except Exception:
                logger.exception("[codex-slack] Slack approval bridge failed")
                return "deny"
            finally:
                with self._action_lock:
                    self._approvals.pop(token, None)

        return callback

    async def _post_approval(self, token: str, pending: _PendingApproval) -> None:
        description = pending.description[:1500]
        command = pending.command[:700]
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Codex 请求批准*\n{description}\n```{command}```",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": "允许一次"}, "style": "primary", "action_id": ACTION_APPROVE_ONCE, "value": token},
                    {"type": "button", "text": {"type": "plain_text", "text": "本 session 允许"}, "action_id": ACTION_APPROVE_SESSION, "value": token},
                    {"type": "button", "text": {"type": "plain_text", "text": "拒绝"}, "style": "danger", "action_id": ACTION_DENY, "value": token},
                ],
            },
        ]
        response = await self._post_message(
            pending.binding.channel_id,
            f"Codex 请求批准：{description}",
            thread_ts=pending.binding.thread_ts,
            blocks=blocks,
        )
        pending.message_ts = str(response.get("ts") or "")

    async def handle_approval_action(self, action_id: str, body: dict, action: dict) -> None:
        channel_id, user_id, team_id = self._action_identity(body)
        token = str(action.get("value") or "")
        with self._action_lock:
            pending = self._approvals.get(token)
        if pending is None:
            await self._post_ephemeral(channel_id, user_id, "该审批已过期。")
            return
        binding = pending.binding
        if (
            channel_id != binding.channel_id
            or (team_id and team_id != binding.team_id)
        ):
            await self._post_ephemeral(channel_id, user_id, "该审批不属于当前 channel。")
            return
        pending.decision = {
            ACTION_APPROVE_ONCE: "once",
            ACTION_APPROVE_SESSION: "session",
            ACTION_DENY: "deny",
        }.get(action_id, "deny")
        pending.event.set()
        await self._post_message(
            channel_id,
            {"once": "已允许一次。", "session": "本 session 已允许。", "deny": "已拒绝。"}[pending.decision],
            thread_ts=binding.thread_ts,
        )

    # ---------- action + inbound routing ----------

    @staticmethod
    def _action_identity(body: dict) -> tuple[str, str, str]:
        channel = body.get("channel") or {}
        user = body.get("user") or {}
        team = body.get("team") or {}
        container = body.get("container") or {}
        return (
            str(channel.get("id") or container.get("channel_id") or ""),
            str(user.get("id") or ""),
            str(team.get("id") or body.get("team_id") or ""),
        )

    async def handle_action(self, action_id: str, body: dict, action: dict) -> None:
        channel_id, user_id, team_id = self._action_identity(body)
        if channel_id != self._home_channel():
            if channel_id and user_id:
                await self._post_ephemeral(channel_id, user_id, "该操作只能在 Codex home channel 执行。")
            return
        state = self._take_action(str(action.get("value") or ""), channel_id=channel_id)
        if state is None or state.kind not in {"page", "resume", "open"}:
            await self._post_ephemeral(channel_id, user_id, "该操作已过期，请重新执行 `/codex sessions`。")
            return
        if state.kind == "page":
            await self.show_sessions(
                channel_id,
                user_id,
                cursor=state.payload.get("cursor"),
                history=state.payload.get("history") or [],
            )
        elif state.kind == "resume":
            await self.resume_by_id(
                str(state.payload["codex_thread_id"]),
                team_id=team_id,
                channel_id=channel_id,
                user_id=user_id,
            )
        else:
            binding = self.store.by_codex(str(state.payload["codex_thread_id"]))
            link = await self._permalink(binding) if binding else None
            if link:
                await self._post_ephemeral(channel_id, user_id, f"<{link}|打开 Codex thread>")
            else:
                await self._post_ephemeral(channel_id, user_id, "原 Slack thread 不存在，请重新 Resume。")

    async def handle_event(self, event: Any) -> None:
        source = getattr(event, "source", None)
        channel_id = str(getattr(source, "chat_id", "") or "")
        user_id = str(getattr(source, "user_id", "") or "")
        text = str(getattr(event, "text", "") or "").strip()
        command_args = _event_command_args(event)
        if command_args is not None:
            await self.handle_command(event)
            return
        thread_ts = str(getattr(source, "thread_id", "") or "")
        if not thread_ts:
            await self._post_ephemeral(channel_id, user_id, self.usage())
            return
        binding = self.store.by_slack(_team_id(event), channel_id, thread_ts)
        if binding is None:
            return
        lowered = text.lower()
        if lowered == "!status":
            await self.status(binding)
        elif lowered == "!stop":
            await self.stop(binding)
        elif text:
            await self.enqueue_turn(binding, text, user_id)
        else:
            await self._post_message(channel_id, "目前只支持文本消息。", thread_ts=thread_ts)

    def pre_dispatch(self, event: Any, gateway: Any) -> Optional[dict[str, str]]:
        source = getattr(event, "source", None)
        if _platform_value(source) != "slack":
            return None
        self.gateway = gateway
        channel_id = str(getattr(source, "chat_id", "") or "")
        home_channel = self._home_channel(gateway)
        text = str(getattr(event, "text", "") or "")
        command = _event_command_args(event) is not None
        if command:
            self._schedule(event)
            return {"action": "skip", "reason": "codex-slack-command"}
        if not home_channel or channel_id != home_channel:
            return None
        thread_ts = str(getattr(source, "thread_id", "") or "")
        if thread_ts:
            binding = self.store.by_slack(_team_id(event), channel_id, thread_ts)
            if binding is None:
                return None
            self._schedule(event)
            return {"action": "skip", "reason": "codex-slack-thread"}
        if text.strip() and not text.lstrip().startswith("/"):
            self._schedule(event)
            return {"action": "skip", "reason": "codex-slack-channel-usage"}
        return None

    def _schedule(self, event: Any) -> None:
        async def runner() -> None:
            await self.handle_event(event)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[codex-slack] No running event loop; event was not scheduled")
            return
        task = loop.create_task(runner())

        def done(finished: asyncio.Task) -> None:
            try:
                finished.result()
            except Exception:
                logger.exception("[codex-slack] Slack handler failed")

        task.add_done_callback(done)


_MANAGER: Optional[CodexSlackManager] = None


def _manager() -> CodexSlackManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CodexSlackManager()
    return _MANAGER


def _pre_gateway_dispatch(event: Any, gateway: Any, **_: Any) -> Optional[dict[str, str]]:
    return _manager().pre_dispatch(event, gateway)


def _usage(raw_args: str) -> str:
    del raw_args
    return CodexSlackManager.usage()


def _action_handler(action_id: str) -> Callable:
    async def handler(ack, body: dict[str, Any], action: dict[str, Any]) -> None:
        await ack()
        manager = _manager()
        if action_id in {ACTION_APPROVE_ONCE, ACTION_APPROVE_SESSION, ACTION_DENY}:
            await manager.handle_approval_action(action_id, body, action)
        else:
            await manager.handle_action(action_id, body, action)

    return handler


def register(ctx) -> None:
    ctx.register_command(
        COMMAND_NAME,
        _usage,
        description="Control local Codex sessions from Slack",
        args_hint="<sessions|new [dir]|resume id [dir]>",
        platforms=("slack",),
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    for action_id in (
        ACTION_PAGE,
        ACTION_RESUME,
        ACTION_OPEN,
        ACTION_APPROVE_ONCE,
        ACTION_APPROVE_SESSION,
        ACTION_DENY,
    ):
        ctx.register_slack_action_handler(action_id, _action_handler(action_id))
