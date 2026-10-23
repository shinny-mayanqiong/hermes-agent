"""Slack control surface for local Codex app-server threads."""

from __future__ import annotations

import asyncio
from functools import partial
import json
import logging
import os
import re
import secrets
import shlex
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from agent.transports.codex_app_server_session import (
    CodexAppServerSession,
    CodexThreadCatalog,
    CodexThreadSummary,
)
from agent.transports.codex_app_server import CodexAppServerClient
from gateway.config import Platform
from hermes_constants import get_hermes_home
from hermes_cli.config import load_config

from .store import Binding, BindingConflict, BindingStore  # type: ignore[unresolved-import]
from .progress import ProgressMirror, turn_progress
from .controls import SessionControls
from .browser import SessionBrowser, ACTION_PAGE, ACTION_RESUME, ACTION_ARCHIVE, ACTION_DELETE

logger = logging.getLogger(__name__)

COMMAND_NAME = "codex"
PROJECT_COMMAND_NAME = "odoo-hedge-codex"

ACTION_OPEN = "codex_slack_open"
ACTION_APPROVE_ONCE = "codex_slack_approve_once"
ACTION_APPROVE_SESSION = "codex_slack_approve_session"
ACTION_DENY = "codex_slack_deny"


@dataclass(frozen=True)
class PluginConfig:
    default_cwd: str = "/home/user/Repos/odoo-hedge"
    allowed_roots: tuple[str, ...] = (
        "/home/user/Repos/odoo-hedge", "/home/user/Repos/odoo-hedge-worktrees",
    )
    page_size: int = 20
    queue_limit: int = 5
    max_live_sessions: int = 5
    idle_timeout_seconds: int = 1800
    approval_timeout_seconds: int = 600
    shared_daemon: bool = False
    daemon_socket: str = ""
    progress_poll_seconds: int = 5


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
    progress_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    has_history: bool = True


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

    def boolean(name: str, default: bool) -> bool:
        value = raw.get(name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    daemon_socket = str(raw.get("daemon_socket") or "").strip()
    if daemon_socket:
        daemon_socket = str(Path(os.path.expanduser(daemon_socket)).absolute())

    return PluginConfig(
        default_cwd=str(Path(os.path.expanduser(default_cwd)).resolve()),
        allowed_roots=normalized_roots,
        page_size=positive_int("page_size", 20, maximum=20),
        queue_limit=positive_int("queue_limit", 5),
        max_live_sessions=positive_int("max_live_sessions", 5),
        idle_timeout_seconds=positive_int("idle_timeout_seconds", 1800),
        approval_timeout_seconds=positive_int("approval_timeout_seconds", 600),
        shared_daemon=boolean("shared_daemon", False),
        daemon_socket=daemon_socket,
        progress_poll_seconds=positive_int("progress_poll_seconds", 5),
    )


def _daemon_client_factory(socket_path: str = "") -> Callable[..., CodexAppServerClient]:
    socket_path = socket_path or str(
        Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
        / "app-server-control"
        / "app-server-control.sock"
    )
    return partial(CodexAppServerClient, unix_socket=socket_path)


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


def _event_text(event: Any, *, bot_user_id: str = "") -> str:
    """Codex already owns history; do not feed Slack's injected transcript back."""
    raw = _raw_message(event)
    if not raw.get("command") and isinstance(raw.get("text"), str):
        text = raw["text"].strip()
        if bot_user_id and text.startswith(f"<@{bot_user_id}>"):
            text = text[len(bot_user_id) + 3:].strip()
        return text
    return str(getattr(event, "text", "") or "").strip()


def _command_args(text: str) -> Optional[str]:
    stripped = str(text or "").strip()
    parts = stripped.split(None, 1)
    if not parts:
        return None
    for name in (COMMAND_NAME, PROJECT_COMMAND_NAME):
        if parts[0].lower() in {f"/{name}", f"!{name}"}:
            return parts[1] if len(parts) == 2 else ("sessions" if name == PROJECT_COMMAND_NAME else "")
    return None


def _event_command_args(event: Any, text: Optional[str] = None) -> Optional[str]:
    """Accept native plugin commands and the already-installed /hermes fallback."""
    text = _event_text(event) if text is None else text
    direct = _command_args(text)
    if direct is not None:
        return direct
    raw = _raw_message(event)
    if str(raw.get("command") or "").strip().lower() != "/hermes":
        return None
    return _command_args("/" + text.strip())


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


class CodexSlackManager(SessionControls, ProgressMirror, SessionBrowser):
    def __init__(
        self,
        *,
        config: Optional[PluginConfig] = None,
        store: Optional[BindingStore] = None,
        catalog: Optional[CodexThreadCatalog] = None,
        session_factory: Optional[Callable[..., CodexAppServerSession]] = None,
    ) -> None:
        self.config = config or _load_plugin_config()
        self.store = store or BindingStore(
            get_hermes_home() / "state" / "codex-slack.sqlite3"
        )
        if self.config.shared_daemon:
            client_factory = _daemon_client_factory(self.config.daemon_socket)
            self.catalog = catalog or CodexThreadCatalog(client_factory=client_factory)
            self.session_factory = session_factory or partial(
                CodexAppServerSession,
                client_factory=client_factory,
                allow_active_turn_steer=True,
                enable_collaboration_modes=True,
            )
        else:
            self.catalog = catalog or CodexThreadCatalog()
            self.session_factory = session_factory or partial(CodexAppServerSession, enable_collaboration_modes=True)
        self.gateway: Any = None
        self._runtimes: OrderedDict[str, _Runtime] = OrderedDict()
        self._actions: dict[str, _ActionState] = {}
        self._approvals: dict[str, _PendingApproval] = {}
        self._action_lock = threading.RLock()
        self._reaper: Optional[asyncio.Task] = None
        self._monitor: Optional[asyncio.Task] = None
        self._handlers: set[asyncio.Task] = set()
        self._shutting_down = False
        self._mirror_locks: dict[str, asyncio.Lock] = {}
        self._control_locks: dict[str, asyncio.Lock] = {}
        self._managing_sessions: set[str] = set()

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

    def _event_team_id(self, event: Any) -> str:
        team = _team_id(event)
        if team:
            return team
        channel_id = str(getattr(getattr(event, "source", None), "chat_id", "") or "")
        return str(getattr(self._adapter(), "_channel_team", {}).get(channel_id, ""))

    def _event_text(self, event: Any) -> str:
        adapter = self._adapter()
        bot_id = getattr(adapter, "_team_bot_user_ids", {}).get(
            self._event_team_id(event), getattr(adapter, "_bot_user_id", "")
        )
        return _event_text(event, bot_user_id=bot_id)

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

    async def _edit_message(self, channel_id: str, message_ts: str, text: str, *, strict: bool = False) -> None:
        if not message_ts:
            return
        adapter = self._adapter()
        client = self._client(adapter, channel_id)
        if client is None:
            if strict:
                raise RuntimeError("Slack client is not available")
            return
        formatter = getattr(adapter, "format_message", None)
        rendered = formatter(text) if callable(formatter) else text
        try:
            await client.chat_update(channel=channel_id, ts=message_ts, text=rendered)
        except Exception:
            if strict:
                raise
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
        if not cwd or not Path(cwd).is_absolute():
            return False
        try:
            # Deleted worktrees must remain discoverable for archive/delete or
            # resuming in a replacement directory. Execution checks existence.
            resolved = Path(cwd).resolve()
        except (OSError, RuntimeError, ValueError):
            return False
        for root in self.config.allowed_roots:
            try:
                resolved.relative_to(Path(root).resolve())
                return True
            except (ValueError, OSError, RuntimeError):
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
                if existing.sync_paused:
                    async with self._control_lock(existing):
                        if not await self.resume_sync(existing):
                            return existing
                await self._post_ephemeral(
                    channel_id,
                    user_id,
                    f"该 Codex session 已绑定：<{link}|打开原 thread>",
                )
                return existing

        if not resumed:
            await self._prune_runtimes(reserve_slot=True)
            if self._live_runtime_count() >= self.config.max_live_sessions:
                raise ValueError("Codex session 数量已达上限，请先完成或停止一个 session。")

        # Prove that the app-server can start/resume before publishing a root.
        runtime = None
        loop = asyncio.get_running_loop()
        session = self.session_factory(
            cwd=summary.cwd,
            resume_thread_id=summary.thread_id if resumed else None,
            approval_callback=lambda *args, **kwargs: (
                self._approval_callback(runtime.binding, loop)(*args, **kwargs) if runtime else "deny"
            ),
            on_event=lambda note: self._on_codex_event(runtime, loop, note) if runtime else None,
        )
        try:
            thread_id = await asyncio.to_thread(session.ensure_started)
        except BaseException:
            await asyncio.to_thread(session.close)
            raise
        if resumed:
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
                "直接在此 thread 回复即可继续；`!mode plan` / `!mode default` 切换模式，`!status` 查看状态。\n`!stop` 停止任务并暂停推送；`!resume` 恢复同步。",
            ]
        )
        try:
            response = await self._post_message(channel_id, root_text)
        except BaseException:
            if not resumed:
                await asyncio.to_thread(session.close)
            raise
        thread_ts = str(response.get("ts") or "")
        if not thread_ts:
            if not resumed:
                await asyncio.to_thread(session.close)
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
            if not resumed:
                await asyncio.to_thread(session.close)
            winner = self.store.by_codex(summary.thread_id)
            if winner:
                await self._post_message(
                    channel_id,
                    "绑定发生并发冲突；请使用 `/codex sessions` 打开已经存在的 thread。",
                    thread_ts=thread_ts,
                )
                return winner
            raise
        if not resumed:
            # An empty thread has no rollout yet. Keep its creating connection
            # alive until the first prompt instead of attempting to resume it.
            runtime = self._runtime(binding)
            runtime.session = session
            runtime.has_history = False
        if self.config.shared_daemon:
            try:
                await self._sync_binding(binding, initialize=True)
            except Exception:
                logger.warning("Unable to seed Codex progress; will retry", exc_info=True)
            self.start(self.gateway)
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
            "`/odoo-hedge-codex` 打开项目会话卡片（也支持下列参数）\n"
            "`/codex sessions [archived]` 查看项目 session\n"
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

        args = _event_command_args(event, self._event_text(event))
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
            if len(argv) > 2 or (len(argv) == 2 and argv[1].lower() != "archived"):
                await self._post_ephemeral(channel_id, user_id, self.usage())
                return
            await self.show_sessions(channel_id, user_id, archived=len(argv) == 2)
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
                    team_id=self._event_team_id(event),
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
                team_id=self._event_team_id(event),
                channel_id=channel_id,
                user_id=user_id,
                cwd_override=" ".join(argv[2:]) or None,
            )
            return
        await self._post_ephemeral(channel_id, user_id, self.usage())

    async def resume_by_id(
        self, thread_id: str, *, team_id: str, channel_id: str, user_id: str,
        cwd_override: Optional[str] = None, restore_archived: bool = False,
    ) -> None:
        if thread_id in self._managing_sessions:
            await self._post_ephemeral(channel_id, user_id, "该会话正在处理操作，请稍后重试。")
            return
        self._managing_sessions.add(thread_id)
        try:
            await self._resume_by_id(thread_id, team_id=team_id, channel_id=channel_id,
                                     user_id=user_id, cwd_override=cwd_override,
                                     restore_archived=restore_archived)
        finally:
            self._managing_sessions.discard(thread_id)

    async def _resume_by_id(
        self,
        thread_id: str,
        *,
        team_id: str,
        channel_id: str,
        user_id: str,
        cwd_override: Optional[str] = None,
        restore_archived: bool = False,
    ) -> None:
        try:
            summary = await asyncio.to_thread(self.catalog.read_thread, thread_id)
        except Exception as exc:
            await self._post_ephemeral(channel_id, user_id, f"无法读取 Codex session：{exc}")
            return
        requested_cwd = cwd_override if cwd_override is not None else summary.cwd
        try:
            if not self._cwd_allowed(summary.cwd):
                raise ValueError("该会话不在允许的项目路径范围内。")
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
            binding = self.store.by_codex(thread_id)
            if restore_archived or (binding and binding.state == "archived"):
                await asyncio.to_thread(self.catalog.unarchive_thread, thread_id)
                if binding:
                    self.store.set_state(binding, "active")
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
            if runtime.has_history and not runtime.active and now - runtime.last_active >= self.config.idle_timeout_seconds:
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
                (key for key, runtime in self._runtimes.items() if runtime.has_history and not runtime.active and runtime.session is not None),
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
        current = self.store.by_codex(binding.codex_thread_id)
        if (not current or current.state != "active"
                or binding.codex_thread_id in self._managing_sessions
                or not self._cwd_allowed(current.cwd)):
            await self._post_ephemeral(binding.channel_id, user_id,
                                       "该会话当前不可运行，请重新打开 `/codex sessions`。")
            return
        if current.sync_paused:
            await self._post_message(binding.channel_id, "此 thread 已暂停，请先 `!resume`，再发送任务。",
                                     thread_ts=binding.thread_ts)
            return
        self._ensure_reaper()
        runtime = self._runtime(binding)
        if runtime.stopping or self._shutting_down:
            await self._post_message(binding.channel_id, "正在停止，请稍后重试。", thread_ts=binding.thread_ts)
            return
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
        # Reserve ownership before yielding so simultaneous replies cannot
        # start two workers against one app-server connection.
        runtime.active = True
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
                # Let an already-running mirror delivery finish before this
                # worker becomes the sole sender for the session.
                lock = self._mirror_locks.setdefault(runtime.binding.codex_thread_id, asyncio.Lock())
                async with lock:
                    pass
                if runtime.stopping:
                    break
                if not await self._ensure_session(runtime):
                    runtime.queue.clear()
                    break
                if runtime.stopping:
                    break
                response = await self._post_message(
                    runtime.binding.channel_id,
                    "⏳ Codex 正在处理…",
                    thread_ts=runtime.binding.thread_ts,
                )
                runtime.progress_ts = str(response.get("ts") or "")
                runtime.progress_updated_at = 0.0
                if runtime.stopping:
                    break
                session = runtime.session
                assert session is not None
                result = await asyncio.to_thread(session.run_turn, item.text)
                runtime.has_history = bool(result.turn_id) or runtime.has_history
                runtime.last_active = time.monotonic()
                self.store.touch(runtime.binding)
                if runtime.stopping:
                    break
                async with runtime.progress_lock:
                    # Serialize final status with any queued progress edit.
                    runtime.progress_updated_at = float("inf")
                if result.error or result.interrupted:
                    await self._edit_message(
                        runtime.binding.channel_id,
                        runtime.progress_ts,
                        "⏹ Codex 已中断。" if result.interrupted else "⚠️ Codex turn 未完成。",
                    )
                    await self._post_message(
                        runtime.binding.channel_id,
                        result.error or "Codex 已中断。",
                        thread_ts=runtime.binding.thread_ts,
                    )
                else:
                    await self._edit_message(
                        runtime.binding.channel_id,
                        runtime.progress_ts,
                        "✅ Codex 已完成。",
                    )
                    await self._send_final(runtime.binding, result.final_text or "Codex 已完成，但没有返回文本。")
                if result.turn_id and self.config.shared_daemon:
                    self.store.save_progress(runtime.binding.codex_thread_id, result.turn_id,
                                             message_ts=runtime.progress_ts, completed=True)
                if result.should_retire:
                    await self._close_runtime(runtime)
        except Exception as exc:
            if runtime.stopping:
                return
            logger.exception("[codex-slack] Codex worker failed")
            runtime.queue.clear()
            async with runtime.progress_lock:
                runtime.progress_updated_at = float("inf")
                await self._edit_message(runtime.binding.channel_id, runtime.progress_ts, "⚠️ Codex 运行失败。")
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
                if self._runtimes.get(runtime.binding.codex_thread_id) is runtime:
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
                result = await result
            if getattr(result, "success", True) is False:
                raise RuntimeError(getattr(result, "error", None) or "Slack delivery failed")
            return
        await self._post_message(binding.channel_id, text, thread_ts=binding.thread_ts)

    def _on_codex_event(self, runtime: _Runtime, loop: asyncio.AbstractEventLoop, note: dict) -> None:
        method = str(note.get("method") or "")
        if method not in {"item/started", "item/completed"}:
            return
        item = (note.get("params") or {}).get("item") or {}
        if item.get("type") not in {"commandExecution", "fileChange", "mcpToolCall", "agentMessage", "plan"}:
            return
        text = "⏳ " + turn_progress({"items": [item]})
        progress_ts = runtime.progress_ts

        def schedule() -> None:
            asyncio.create_task(self._update_progress(runtime, text, progress_ts=progress_ts))

        loop.call_soon_threadsafe(schedule)

    async def _update_progress(self, runtime: _Runtime, text: str, *, progress_ts: str = "") -> None:
        async with runtime.progress_lock:
            if not runtime.active or runtime.stopping or (progress_ts and progress_ts != runtime.progress_ts):
                return
            now = time.monotonic()
            if now - runtime.progress_updated_at < 1.0:
                return
            runtime.progress_updated_at = now
            await self._edit_message(runtime.binding.channel_id, runtime.progress_ts, text)

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
        lock = self._mirror_locks.setdefault(pending.binding.codex_thread_id, asyncio.Lock())
        async with lock:
            await self._post_approval_locked(token, pending)

    async def _post_approval_locked(self, token: str, pending: _PendingApproval) -> None:
        current = self.store.by_codex(pending.binding.codex_thread_id)
        if not current or current.sync_paused:
            pending.event.set()
            return
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
        if (state is None or state.kind not in {"page", "resume", "open", "archive", "delete"}
                or action_id.split(":", 1)[0] != f"codex_slack_{state.kind}"):
            await self._post_ephemeral(channel_id, user_id, "该操作已过期，请重新执行 `/codex sessions`。")
            return
        if state.kind == "page":
            await self.show_sessions(
                channel_id,
                user_id,
                cursor=state.payload.get("cursor"),
                history=state.payload.get("history") or [],
                archived=bool(state.payload.get("archived")),
            )
        elif state.kind == "resume":
            await self.resume_by_id(
                str(state.payload["codex_thread_id"]),
                team_id=team_id,
                channel_id=channel_id,
                user_id=user_id,
                restore_archived=bool(state.payload.get("archived")),
            )
        elif state.kind in {"archive", "delete"}:
            await self.manage_session(
                state.kind, str(state.payload["codex_thread_id"]),
                channel_id=channel_id, user_id=user_id,
                archived=bool(state.payload.get("archived")),
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
        text = self._event_text(event)
        command_args = _event_command_args(event, text)
        if command_args is not None:
            await self.handle_command(event)
            return
        thread_ts = str(getattr(source, "thread_id", "") or "")
        if not thread_ts:
            await self._post_ephemeral(channel_id, user_id, self.usage())
            return
        binding = self.store.by_slack(self._event_team_id(event), channel_id, thread_ts)
        if binding is None:
            return
        if binding.state != "active":
            text = ("该会话已归档；请在 `/codex sessions archived` 中点击 Resume 恢复。"
                    if binding.state == "archived" else "该 Codex 会话已永久删除，原 Slack 消息仍保留。")
            await self._post_ephemeral(channel_id, user_id, text, thread_ts=thread_ts)
            return
        if not self._cwd_allowed(binding.cwd):
            await self._post_ephemeral(channel_id, user_id, "该会话不在允许的项目路径范围内。", thread_ts=thread_ts)
            return
        async with self._control_lock(binding):
            lowered = text.lower()
            if lowered == "!status":
                await self.status(binding)
            elif lowered == "!stop":
                await self.stop(binding)
            elif lowered == "!resume":
                await self.resume_sync(binding)
            elif lowered.split(None, 1)[0:1] == ["!mode"]:
                parts = lowered.split(None, 1)
                await self.mode(binding, parts[1] if len(parts) > 1 else "")
            elif text.startswith("!"):
                await self._post_message(channel_id,
                    "控制命令：`!mode [plan|default]`、`!status`、`!stop`、`!resume`。",
                    thread_ts=thread_ts)
            elif text:
                await self.enqueue_turn(binding, text, user_id)
            else:
                await self._post_message(channel_id, "目前只支持文本消息。", thread_ts=thread_ts)

    def pre_dispatch(self, event: Any, gateway: Any) -> Optional[dict[str, str]]:
        source = getattr(event, "source", None)
        if _platform_value(source) != "slack":
            return None
        self.gateway = gateway
        if self._shutting_down:
            return None
        channel_id = str(getattr(source, "chat_id", "") or "")
        home_channel = self._home_channel(gateway)
        text = self._event_text(event)
        command = _event_command_args(event, text) is not None
        if command:
            self._schedule(event)
            return {"action": "skip", "reason": "codex-slack-command"}
        if not home_channel or channel_id != home_channel:
            return None
        thread_ts = str(getattr(source, "thread_id", "") or "")
        if thread_ts:
            binding = self.store.by_slack(self._event_team_id(event), channel_id, thread_ts)
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
        self._handlers.add(task)

        def done(finished: asyncio.Task) -> None:
            self._handlers.discard(finished)
            if finished.cancelled():
                return
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


def _on_gateway_start(gateway: Any, **_: Any) -> None:
    _manager().start(gateway)


async def _on_gateway_stop(**_: Any) -> None:
    if _MANAGER is not None:
        await _MANAGER.shutdown()


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
        args_hint="<sessions [archived]|new [dir]|resume id [dir]>",
        platforms=("slack",),
    )
    ctx.register_command(
        PROJECT_COMMAND_NAME,
        _usage,
        description="Odoo Hedge：查看、创建或继续 Codex 会话",
        args_hint="[sessions [archived] | new [dir] | resume id [dir]]",
        platforms=("slack",),
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_hook("on_gateway_start", _on_gateway_start)
    ctx.register_hook("on_gateway_stop", _on_gateway_stop)
    for action_id in (
        ACTION_PAGE,
        ACTION_RESUME,
        ACTION_ARCHIVE,
        ACTION_DELETE,
        ACTION_OPEN,
        ACTION_APPROVE_ONCE,
        ACTION_APPROVE_SESSION,
        ACTION_DENY,
    ):
        pattern = (re.compile(rf"^{action_id}(?::[A-Za-z0-9_-]+)?$")
                   if action_id in {ACTION_PAGE, ACTION_RESUME, ACTION_ARCHIVE, ACTION_DELETE}
                   else action_id)
        ctx.register_slack_action_handler(pattern, _action_handler(action_id))
