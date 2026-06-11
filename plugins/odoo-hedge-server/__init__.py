"""Slack workflow bootstrap for Odoo Hedge Server operations.

Step 1 intentionally stops at Slack routing and intent collection. A future
version can replace the echo reply with an HTTP call once the service contract
is available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_replace

logger = logging.getLogger(__name__)

COMMAND_NAME = "odoo-hedge-server"
COMMAND_PREFIX = f"/{COMMAND_NAME}"
ALT_COMMAND_PREFIX = f"!{COMMAND_NAME}"

_STATE_LOCK = asyncio.Lock()

_INTENT_LABELS = {
    "create": "创建服务",
    "destroy": "销毁服务",
    "upgrade": "升级服务",
    "status": "列状态",
    "unknown": "未识别",
}

_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9_.])v?(?P<major>1[0-9]|2[0-9])(?:\.(?P<minor>\d+))?(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)


def _state_path() -> Path:
    return get_hermes_home() / "state" / "odoo-hedge-server-threads.json"


def _empty_state() -> dict[str, Any]:
    return {"threads": {}}


async def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[odoo-hedge-server] Failed to read state file: %s", path)
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    threads = data.get("threads")
    if not isinstance(threads, dict):
        data["threads"] = {}
    return data


async def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, path)


def _platform_value(source: Any) -> str:
    return str(getattr(getattr(source, "platform", None), "value", getattr(source, "platform", "")) or "").lower()


def _raw_dict(event: Any) -> dict[str, Any]:
    raw = getattr(event, "raw_message", None)
    return raw if isinstance(raw, dict) else {}


def _team_id(event: Any) -> str:
    raw = _raw_dict(event)
    source = getattr(event, "source", None)
    return str(
        raw.get("team_id")
        or raw.get("team")
        or getattr(source, "guild_id", None)
        or ""
    )


def _thread_key(event: Any, thread_ts: str | None = None) -> str:
    source = getattr(event, "source", None)
    return ":".join(
        [
            _team_id(event),
            str(getattr(source, "chat_id", "") or ""),
            str(thread_ts or getattr(source, "thread_id", "") or ""),
        ]
    )


def _command_args(text: str) -> str | None:
    stripped = (text or "").strip()
    lower = stripped.lower()
    for prefix in (COMMAND_PREFIX, ALT_COMMAND_PREFIX):
        if lower == prefix:
            return ""
        if lower.startswith(prefix + " "):
            return stripped[len(prefix):].strip()
    return None


def _parse_version(text: str) -> str:
    match = _VERSION_RE.search(text or "")
    if not match:
        return ""
    major = match.group("major")
    minor = match.group("minor")
    return f"{major}.{minor}" if minor is not None else f"{major}.0"


def _parse_intent(text: str) -> str:
    lowered = (text or "").lower()
    if any(token in lowered for token in ("状态", "列状态", "查看", "list", "status")):
        return "status"
    if any(token in lowered for token in ("升级", "更新", "upgrade")):
        return "upgrade"
    if any(token in lowered for token in ("销毁", "删除", "关闭", "停止", "destroy", "delete", "remove")):
        return "destroy"
    if any(token in lowered for token in ("创建", "部署", "新建", "启动", "create", "deploy", "start")):
        return "create"
    return "unknown"


def _parse_request(text: str) -> dict[str, str]:
    return {
        "intent": _parse_intent(text),
        "version": _parse_version(text),
        "raw_text": (text or "").strip(),
    }


def _needs_version(intent: str) -> bool:
    return intent in {"create", "upgrade"}


def _format_user(user_name: str, user_id: str) -> str:
    if user_name and user_id and user_name != user_id:
        return f"{user_name} (`{user_id}`)"
    return user_id or user_name or "unknown"


def _format_echo(record: dict[str, Any]) -> str:
    intent = str(record.get("intent") or "unknown")
    version = str(record.get("version") or "")
    raw_text = str(record.get("raw_text") or "")
    lines = [
        "已收到 Odoo Hedge Server 请求：",
        f"- 用户：{_format_user(str(record.get('user_name') or ''), str(record.get('user_id') or ''))}",
        f"- 意图：{_INTENT_LABELS.get(intent, intent)}",
        f"- 部署版本：{version or '未提供'}",
    ]
    if raw_text:
        lines.append(f"- 原始请求：{raw_text}")
    lines.append("")
    lines.append("HTTP 服务尚未接入；当前步骤只完成 Slack thread 和参数收集。")
    return "\n".join(lines)


def _format_version_prompt(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            "已创建 Odoo Hedge Server 操作线程。",
            f"用户：{_format_user(str(record.get('user_name') or ''), str(record.get('user_id') or ''))}",
            f"我理解你的意图是：{_INTENT_LABELS.get(str(record.get('intent') or 'unknown'), '未识别')}。",
            "请在此 thread 回复想要部署的 Odoo 版本，例如 `17.0`。",
        ]
    )


async def _resolve_user_name(adapter: Any, event: Any) -> str:
    source = getattr(event, "source", None)
    user_name = str(getattr(source, "user_name", "") or "")
    if user_name:
        return user_name
    user_id = str(getattr(source, "user_id", "") or "")
    resolver = getattr(adapter, "_resolve_user_name", None)
    if resolver and user_id:
        try:
            resolved = resolver(user_id, chat_id=getattr(source, "chat_id", ""))
            if asyncio.iscoroutine(resolved):
                resolved = await resolved
            if resolved:
                return str(resolved)
        except Exception as exc:
            logger.debug("[odoo-hedge-server] Slack user-name lookup failed: %s", exc)
    return user_id


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


async def _replace_slash_ack(adapter: Any, chat_id: str, content: str) -> None:
    pop_ctx = getattr(adapter, "_pop_slash_context", None)
    send_ephemeral = getattr(adapter, "_send_slash_ephemeral", None)
    if not pop_ctx or not send_ephemeral:
        return
    try:
        ctx = pop_ctx(chat_id)
        if ctx:
            await send_ephemeral(ctx, content)
    except Exception as exc:
        logger.debug("[odoo-hedge-server] Failed to replace Slack slash ack: %s", exc)


def _base_record(event: Any, user_name: str, parsed: dict[str, str]) -> dict[str, Any]:
    source = getattr(event, "source", None)
    return {
        "team_id": _team_id(event),
        "channel_id": str(getattr(source, "chat_id", "") or ""),
        "thread_ts": str(getattr(source, "thread_id", "") or ""),
        "user_id": str(getattr(source, "user_id", "") or ""),
        "user_name": user_name,
        "intent": parsed.get("intent", "unknown"),
        "version": parsed.get("version", ""),
        "raw_text": parsed.get("raw_text", ""),
        "created_at": time.time(),
        "updated_at": time.time(),
        "pending": "",
    }


def _register_gateway_thread_session(event: Any, gateway: Any, thread_ts: str) -> None:
    session_store = getattr(gateway, "session_store", None)
    if session_store is None or not hasattr(session_store, "get_or_create_session"):
        return
    source = getattr(event, "source", None)
    try:
        from gateway.session import SessionSource

        session_store.get_or_create_session(
            SessionSource(
                platform=getattr(source, "platform", None),
                chat_id=str(getattr(source, "chat_id", "") or ""),
                chat_name=getattr(source, "chat_name", None),
                chat_type=str(getattr(source, "chat_type", "") or "group"),
                user_id=str(getattr(source, "user_id", "") or "") or None,
                user_name=getattr(source, "user_name", None),
                thread_id=thread_ts,
                guild_id=_team_id(event) or None,
            )
        )
    except Exception as exc:
        logger.debug("[odoo-hedge-server] Failed to register gateway thread session: %s", exc)


async def _handle_command_start(event: Any, gateway: Any, args: str) -> None:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    adapter = gateway.adapters.get(getattr(source, "platform", None))
    if adapter is None or not chat_id:
        logger.warning("[odoo-hedge-server] Slack adapter/chat missing for command start")
        return

    user_name = await _resolve_user_name(adapter, event)
    parsed = _parse_request(args)
    record = _base_record(event, user_name, parsed)

    root_text = "\n".join(
        [
            "Odoo Hedge Server 操作线程已开启。",
            f"发起人：{_format_user(record['user_name'], record['user_id'])}",
            "请在此 thread 内继续补充创建、销毁、升级或列状态需求。",
        ]
    )
    if args.strip():
        root_text += f"\n初始请求：{args.strip()}"

    root_response = await _post_slack_message(adapter, chat_id, root_text)
    thread_ts = str(root_response.get("ts") or "")
    if not thread_ts:
        raise RuntimeError("Slack did not return a thread root timestamp")
    record["thread_ts"] = thread_ts
    record["updated_at"] = time.time()
    _register_gateway_thread_session(event, gateway, thread_ts)

    key = _thread_key(event, thread_ts=thread_ts)
    async with _STATE_LOCK:
        state = await _load_state()
        state.setdefault("threads", {})[key] = record
        await _save_state(state)

    _remember_slack_thread(adapter, thread_ts)
    await _replace_slash_ack(
        adapter,
        chat_id,
        "已创建 Odoo Hedge Server thread，请在新 thread 中继续。",
    )

    if _needs_version(record["intent"]) and not record["version"]:
        record["pending"] = "version"
        async with _STATE_LOCK:
            state = await _load_state()
            state.setdefault("threads", {})[key] = record
            await _save_state(state)
        reply = _format_version_prompt(record)
    else:
        reply = _format_echo(record)

    reply_response = await _post_slack_message(adapter, chat_id, reply, thread_ts=thread_ts)
    _remember_slack_thread(adapter, thread_ts, str(reply_response.get("ts") or ""))


async def _handle_thread_message(event: Any, gateway: Any, key: str, record: dict[str, Any]) -> None:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    thread_ts = str(getattr(source, "thread_id", "") or "")
    adapter = gateway.adapters.get(getattr(source, "platform", None))
    if adapter is None or not chat_id or not thread_ts:
        return

    text = str(getattr(event, "text", "") or "").strip()
    if text in {"取消", "结束", "cancel", "stop", "done"}:
        async with _STATE_LOCK:
            state = await _load_state()
            state.setdefault("threads", {}).pop(key, None)
            await _save_state(state)
        await _post_slack_message(adapter, chat_id, "Odoo Hedge Server 操作线程已结束。", thread_ts=thread_ts)
        return

    parsed = _parse_request(text)
    if parsed["intent"] == "unknown":
        parsed["intent"] = str(record.get("intent") or "unknown")
    if not parsed["version"]:
        parsed["version"] = str(record.get("version") or "")
    user_name = await _resolve_user_name(adapter, event)
    updated = dict(record)
    updated.update(
        {
            "user_id": str(getattr(source, "user_id", "") or record.get("user_id") or ""),
            "user_name": user_name or str(record.get("user_name") or ""),
            "intent": parsed["intent"],
            "version": parsed["version"],
            "raw_text": text or str(record.get("raw_text") or ""),
            "updated_at": time.time(),
        }
    )

    if _needs_version(updated["intent"]) and not updated["version"]:
        updated["pending"] = "version"
        reply = _format_version_prompt(updated)
    else:
        updated["pending"] = ""
        reply = _format_echo(updated)

    async with _STATE_LOCK:
        state = await _load_state()
        state.setdefault("threads", {})[key] = updated
        await _save_state(state)

    response = await _post_slack_message(adapter, chat_id, reply, thread_ts=thread_ts)
    _remember_slack_thread(adapter, thread_ts, str(response.get("ts") or ""))


async def _handle_odoo_event(event: Any, gateway: Any) -> None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return

    text = str(getattr(event, "text", "") or "")
    args = _command_args(text)
    if args is not None:
        await _handle_command_start(event, gateway, args)
        return

    thread_ts = str(getattr(source, "thread_id", "") or "")
    if not thread_ts:
        return
    key = _thread_key(event)
    async with _STATE_LOCK:
        state = await _load_state()
        record = state.setdefault("threads", {}).get(key)
    if isinstance(record, dict):
        await _handle_thread_message(event, gateway, key, record)


def _schedule_odoo_event(event: Any, gateway: Any) -> None:
    async def _runner() -> None:
        await _handle_odoo_event(event, gateway)

    task = asyncio.create_task(_runner())

    def _done(done: asyncio.Task) -> None:
        try:
            done.result()
        except Exception as exc:
            logger.warning("[odoo-hedge-server] Slack workflow failed: %s", exc, exc_info=True)

    task.add_done_callback(_done)


def _pre_gateway_dispatch(event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return None

    # pre_gateway_dispatch runs before normal gateway auth. Do not let this
    # workflow become an auth bypass.
    auth = getattr(gateway, "_is_user_authorized", None)
    if auth is not None:
        try:
            if not auth(source):
                return {"action": "allow"}
        except Exception as exc:
            logger.debug("[odoo-hedge-server] Auth check failed, falling through: %s", exc)
            return {"action": "allow"}

    text = str(getattr(event, "text", "") or "")
    if _command_args(text) is not None:
        _schedule_odoo_event(event, gateway)
        return {"action": "skip", "reason": "odoo-hedge-server-command"}

    thread_ts = str(getattr(source, "thread_id", "") or "")
    if not thread_ts:
        return None

    async def _is_active_thread() -> bool:
        async with _STATE_LOCK:
            state = await _load_state()
            return _thread_key(event) in state.setdefault("threads", {})

    async def _runner_if_active() -> None:
        if await _is_active_thread():
            await _handle_odoo_event(event, gateway)

    # We need to know synchronously whether to skip gateway dispatch. Reading
    # the small JSON state file here keeps the command path deterministic.
    path = _state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        active = _thread_key(event) in (state.get("threads") or {})
    except Exception:
        active = False
    if not active:
        return None
    task = asyncio.create_task(_runner_if_active())
    task.add_done_callback(
        lambda done: logger.warning(
            "[odoo-hedge-server] Slack thread handler failed: %s",
            done.exception(),
            exc_info=True,
        )
        if not done.cancelled() and done.exception()
        else None
    )
    return {"action": "skip", "reason": "odoo-hedge-server-thread"}


def _usage(raw_args: str) -> str:
    del raw_args
    return (
        "Usage: `/odoo-hedge-server 创建一个 17.0 服务`\n"
        "Slack 中会创建一个专用 thread，后续直接在该 thread 回复即可。"
    )


def register(ctx) -> None:
    ctx.register_command(
        COMMAND_NAME,
        _usage,
        description="Start an Odoo Hedge Server Slack workflow",
        args_hint="<创建|销毁|升级|状态> [版本]",
        platforms=("slack",),
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
