"""Slack entrypoint for Odoo Hedge PR review jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from . import workflow

logger = logging.getLogger(__name__)

COMMAND_NAME = "pr-review"
COMMAND_PREFIX = f"/{COMMAND_NAME}"
ALT_COMMAND_PREFIX = f"!{COMMAND_NAME}"


def _platform_value(source: Any) -> str:
    return str(getattr(getattr(source, "platform", None), "value", getattr(source, "platform", "")) or "").lower()


def _command_args(text: str) -> str | None:
    stripped = (text or "").strip()
    lower = stripped.lower()
    for prefix in (COMMAND_PREFIX, ALT_COMMAND_PREFIX):
        if lower == prefix:
            return ""
        if lower.startswith(prefix + " "):
            return stripped[len(prefix):].strip()
    return None


def _event_command_args(event: Any) -> str | None:
    args = _command_args(str(getattr(event, "text", "") or ""))
    if args is not None:
        return args

    raw = getattr(event, "raw_message", None)
    if not isinstance(raw, dict):
        return None
    raw_command = str(raw.get("command") or "").strip().lower().lstrip("/")
    if raw_command != "hermes":
        return None
    raw_text = str(raw.get("text") or "").strip()
    if not raw_text:
        return None
    return _command_args("/" + raw_text.lstrip("/"))


def _slack_client(adapter: Any, chat_id: str) -> Any:
    getter = getattr(adapter, "_get_client", None)
    if getter:
        return getter(chat_id)
    app = getattr(adapter, "_app", None)
    return getattr(app, "client", None)


def _slack_response_data(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    getter = getattr(response, "get", None)
    if callable(getter):
        result: dict[str, Any] = {}
        for key in ("ok", "ts", "channel", "message", "error"):
            value = getter(key)
            if value is not None:
                result[key] = value
        return result
    return {}


async def _post_slack_message(
    adapter: Any,
    chat_id: str,
    content: str,
    *,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    client = _slack_client(adapter, chat_id)
    if client is None:
        raise RuntimeError("Slack client is not available")
    formatter = getattr(adapter, "format_message", None)
    text = formatter(content) if formatter else content
    kwargs: dict[str, Any] = {"channel": chat_id, "text": text, "mrkdwn": True}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    response = await client.chat_postMessage(**kwargs)
    return _slack_response_data(response)


def _remember_slack_thread(adapter: Any, thread_ts: str, message_ts: str = "") -> None:
    bot_ts = getattr(adapter, "_bot_message_ts", None)
    if hasattr(bot_ts, "add"):
        bot_ts.add(thread_ts)
        if message_ts:
            bot_ts.add(message_ts)
    mentioned = getattr(adapter, "_mentioned_threads", None)
    if hasattr(mentioned, "add"):
        mentioned.add(thread_ts)


def _discard_slash_context(adapter: Any, chat_id: str) -> None:
    pop_ctx = getattr(adapter, "_pop_slash_context", None)
    if not pop_ctx:
        return
    try:
        pop_ctx(chat_id)
    except Exception as exc:
        logger.debug("[odoo-hedge-workflow] Failed to discard Slack slash context: %s", exc)


def _format_requester(source: Any) -> str:
    user_name = str(getattr(source, "user_name", "") or "")
    user_id = str(getattr(source, "user_id", "") or "")
    if user_name and user_id and user_name != user_id:
        return f"{user_name} ({user_id})"
    return user_id or user_name or "unknown"


def _usage() -> str:
    return "Usage: `/pr-review <PR_URL|PR_NUMBER> [extra review focus]`"


def _board() -> str:
    return os.environ.get("ODOO_HEDGE_PR_REVIEW_BOARD", workflow.PR_REVIEW_DEFAULT_BOARD).strip() or workflow.PR_REVIEW_DEFAULT_BOARD


def _repo() -> str:
    return os.environ.get("ODOO_HEDGE_PR_REVIEW_REPO", workflow.PR_REVIEW_DEFAULT_REPO).strip() or workflow.PR_REVIEW_DEFAULT_REPO


def _subscribe_to_task(
    *,
    gateway: Any,
    event: Any,
    task_id: str,
    board: str,
    thread_ts: str,
) -> None:
    from hermes_cli import kanban_db as kb

    source = getattr(event, "source", None)
    platform = _platform_value(source)
    chat_id = str(getattr(source, "chat_id", "") or "")
    user_id = str(getattr(source, "user_id", "") or "") or None
    notifier_profile = getattr(gateway, "_kanban_notifier_profile", None)
    if not notifier_profile:
        active_profile = getattr(gateway, "_active_profile_name", None)
        notifier_profile = active_profile() if callable(active_profile) else active_profile
    notifier_profile = notifier_profile or "default"
    with kb.connect_closing(board=board) as conn:
        kb.add_notify_sub(
            conn,
            task_id=task_id,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_ts,
            user_id=user_id,
            notifier_profile=notifier_profile,
        )


async def _thread_for_request(event: Any, adapter: Any, chat_id: str, args: str) -> str:
    source = getattr(event, "source", None)
    thread_ts = str(getattr(source, "thread_id", "") or "")
    if thread_ts:
        return thread_ts
    root = await _post_slack_message(
        adapter,
        chat_id,
        "\n".join(
            [
                "Odoo Hedge PR review requested.",
                f"Request: `{args.strip() or '(missing)'}`",
                "Preparing an isolated review task...",
            ]
        ),
    )
    thread_ts = str(root.get("ts") or "")
    if not thread_ts:
        raise RuntimeError("Slack did not return a thread root timestamp")
    _remember_slack_thread(adapter, thread_ts)
    return thread_ts


async def _handle_slack_pr_review_event(event: Any, gateway: Any, args: str) -> None:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    adapter = gateway.adapters.get(getattr(source, "platform", None))
    if adapter is None or not chat_id:
        logger.warning("[odoo-hedge-workflow] Slack adapter/chat missing for pr-review")
        return

    _discard_slash_context(adapter, chat_id)
    thread_ts = await _thread_for_request(event, adapter, chat_id, args)
    if not args.strip():
        response = await _post_slack_message(adapter, chat_id, _usage(), thread_ts=thread_ts)
        _remember_slack_thread(adapter, thread_ts, str(response.get("ts") or ""))
        return

    try:
        board = _board()
        result = await asyncio.to_thread(
            workflow.start_pr_review,
            args,
            board=board,
            repo=_repo(),
            requester=_format_requester(source),
        )
        await asyncio.to_thread(
            _subscribe_to_task,
            gateway=gateway,
            event=event,
            task_id=result["task_id"],
            board=board,
            thread_ts=thread_ts,
        )
    except Exception as exc:
        logger.warning("[odoo-hedge-workflow] PR review start failed: %s", exc, exc_info=True)
        response = await _post_slack_message(
            adapter,
            chat_id,
            f"PR review failed to start: `{exc}`",
            thread_ts=thread_ts,
        )
        _remember_slack_thread(adapter, thread_ts, str(response.get("ts") or ""))
        return

    lines = [
        "Odoo Hedge PR review started.",
        f"Kanban task: `{result['task_id']}`",
        f"PR: {result['pr_url']}",
        f"Worktree: `{result['worktree']}`",
        "This thread is subscribed to completion and blocked notifications.",
    ]
    response = await _post_slack_message(adapter, chat_id, "\n".join(lines), thread_ts=thread_ts)
    _remember_slack_thread(adapter, thread_ts, str(response.get("ts") or ""))


def _schedule_pr_review_event(event: Any, gateway: Any, args: str) -> None:
    async def _runner() -> None:
        await _handle_slack_pr_review_event(event, gateway, args)

    task = asyncio.create_task(_runner())

    def _done(done: asyncio.Task) -> None:
        try:
            done.result()
        except Exception as exc:
            logger.warning("[odoo-hedge-workflow] Slack PR review workflow failed: %s", exc, exc_info=True)

    task.add_done_callback(_done)


def pre_gateway_dispatch(event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return None

    auth = getattr(gateway, "_is_user_authorized", None)
    if auth is not None:
        try:
            if not auth(source):
                return {"action": "allow"}
        except Exception as exc:
            logger.debug("[odoo-hedge-workflow] Auth check failed, falling through: %s", exc)
            return {"action": "allow"}

    args = _event_command_args(event)
    if args is None:
        return None
    _schedule_pr_review_event(event, gateway, args)
    return {"action": "skip", "reason": "odoo-hedge-pr-review-command"}


def pr_review_command(_: str) -> str:
    return (
        "Use `/pr-review <PR_URL|PR_NUMBER> [extra review focus]` from Slack, "
        "or `hermes odoo-hedge-workflow pr-review <PR_URL|PR_NUMBER>` locally."
    )
