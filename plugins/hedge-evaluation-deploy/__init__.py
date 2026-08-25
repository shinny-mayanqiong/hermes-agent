"""Slack entry point for the fixed Hedge evaluation environment."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_replace

logger = logging.getLogger(__name__)

COMMAND_NAME = "hedge-evaluation-deploy"
COMMAND_PREFIX = f"/{COMMAND_NAME}"
ALT_COMMAND_PREFIX = f"!{COMMAND_NAME}"
SKILL_NAME = "hedge-evaluation-deploy"
_SENSITIVE_INPUT_PREFIX = (
    "[Sensitive evaluation-deployment input. Never echo broker credentials or "
    "copy them into logs, files, or memory; the user text follows.]\n\n"
)
_SENSITIVE_REPLY_PLACEHOLDER = "[sensitive evaluation-deployment thread message]"
_AUTHORITATIVE_BROKER_JSON_MARKER = (
    "[Authoritative broker_json extracted verbatim from exactly one Slack "
    "code block; content follows unchanged.]\n"
)

_STATE_LOCK = asyncio.Lock()


def _state_path() -> Path:
    return get_hermes_home() / "state" / "hedge-evaluation-deploy-threads.json"


def _empty_state() -> dict[str, Any]:
    return {"threads": {}}


async def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[hedge-evaluation-deploy] Failed to read state file: %s", path)
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    if not isinstance(data.get("threads"), dict):
        data["threads"] = {}
    return data


async def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, path)


def _platform_value(source: Any) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "").lower()


def _raw_dict(event: Any) -> dict[str, Any]:
    raw = getattr(event, "raw_message", None)
    return raw if isinstance(raw, dict) else {}


def _team_id(event: Any) -> str:
    raw = _raw_dict(event)
    source = getattr(event, "source", None)
    return str(raw.get("team_id") or raw.get("team") or getattr(source, "guild_id", None) or "")


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
    lowered = stripped.lower()
    for prefix in (COMMAND_PREFIX, ALT_COMMAND_PREFIX):
        if lowered == prefix:
            return ""
        if lowered.startswith(prefix + " "):
            return stripped[len(prefix) :].strip()
    return None


def _format_user(user_name: str, user_id: str) -> str:
    if user_name and user_id and user_name != user_id:
        return f"{user_name} (`{user_id}`)"
    return user_id or user_name or "unknown"


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
            logger.debug("[hedge-evaluation-deploy] Slack user lookup failed: %s", exc)
    return user_id


def _slack_client(adapter: Any, chat_id: str) -> Any:
    getter = getattr(adapter, "_get_client", None)
    if getter:
        return getter(chat_id)
    return getattr(getattr(adapter, "_app", None), "client", None)


def _slack_response_data(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    getter = getattr(response, "get", None)
    if callable(getter):
        result = {}
        for key in ("ok", "ts", "error"):
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
    return _slack_response_data(await client.chat_postMessage(**kwargs))


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
    pop_context = getattr(adapter, "_pop_slash_context", None)
    if not pop_context:
        return
    try:
        pop_context(chat_id)
    except Exception as exc:
        logger.debug("[hedge-evaluation-deploy] Failed to discard slash context: %s", exc)


def _base_record(event: Any, user_name: str) -> dict[str, Any]:
    source = getattr(event, "source", None)
    return {
        "team_id": _team_id(event),
        "channel_id": str(getattr(source, "chat_id", "") or ""),
        "thread_ts": "",
        "user_id": str(getattr(source, "user_id", "") or ""),
        "user_name": user_name,
        "workflow": "agent",
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def _merge_auto_skill(existing: Any) -> str | list[str]:
    names = [] if not existing else ([existing] if isinstance(existing, str) else list(existing))
    deduped: list[str] = []
    for name in [*names, SKILL_NAME]:
        if isinstance(name, str) and name and name not in deduped:
            deduped.append(name)
    return deduped[0] if len(deduped) == 1 else deduped


def _sensitive_agent_text(text: str) -> str:
    if text.startswith(_SENSITIVE_INPUT_PREFIX):
        return text
    return f"{_SENSITIVE_INPUT_PREFIX}{text}"


def _render_slack_preformatted_content(element: dict[str, Any]) -> str:
    """Reassemble a Slack preformatted block without mrkdwn wrappers."""

    def _render(elements: Any) -> str:
        if not isinstance(elements, list):
            return ""
        pieces: list[str] = []
        for child in elements:
            if not isinstance(child, dict):
                continue
            child_type = child.get("type")
            if child_type == "text":
                pieces.append(str(child.get("text") or ""))
            elif child_type == "link":
                # Slack sometimes tokenizes a URI even inside a preformatted
                # block. Its text is the exact user-visible token; fall back
                # to the URL when Slack omits that field.
                pieces.append(str(child.get("text") or child.get("url") or ""))
            elif child_type in {"rich_text_section", "rich_text_preformatted"}:
                pieces.append(_render(child.get("elements")))
        return "".join(pieces)

    return _render(element.get("elements"))


def _valid_broker_json_code_blocks(event: Any) -> list[str]:
    """Return exact Slack code-block contents that are top-level JSON arrays."""
    raw = _raw_dict(event)
    blocks = raw.get("blocks")
    if not isinstance(blocks, list):
        return []

    candidates: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "rich_text_preformatted":
            content = _render_slack_preformatted_content(value)
            try:
                parsed = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                return
            if isinstance(parsed, list):
                candidates.append(content)
            return
        _walk(value.get("elements"))

    _walk(blocks)
    return candidates


def _thread_channel_prompt(
    record: dict[str, Any],
    broker_code_block_count: int,
) -> str:
    requester = _format_user(str(record.get("user_name") or ""), str(record.get("user_id") or ""))
    return "\n".join(
        [
            "[Hedge evaluation deployment Slack thread]",
            "This thread was opened by /hedge-evaluation-deploy. Treat its messages as fixed evaluation-environment requests.",
            f"Slack requester: {requester}. Only this requester may confirm deployment or cancellation unless they delegate explicitly.",
            "broker.json is sensitive: do not echo raw JSON or credentials, and do not deliberately copy them to thread state, files, or memory.",
            "Accept broker.json only from exactly one Slack code block in the current message. Plain JSON, command arguments, and multiple code blocks are not deployment payloads.",
            f"Valid top-level JSON-array code blocks in the current message: {broker_code_block_count}.",
            "Users do not need to repeat /hedge-evaluation-deploy in this thread.",
        ]
    )


def _apply_thread_context(event: Any, record: dict[str, Any]) -> None:
    broker_code_blocks = _valid_broker_json_code_blocks(event)
    try:
        raw_text = str(getattr(event, "text", "") or "")
        if len(broker_code_blocks) == 1:
            raw_text = (
                f"{_AUTHORITATIVE_BROKER_JSON_MARKER}"
                f"{broker_code_blocks[0]}"
            )
        event.text = _sensitive_agent_text(raw_text)
    except Exception:
        pass
    try:
        if getattr(event, "reply_to_text", None):
            event.reply_to_text = _SENSITIVE_REPLY_PLACEHOLDER
    except Exception:
        pass
    try:
        event.auto_skill = _merge_auto_skill(getattr(event, "auto_skill", None))
    except Exception:
        pass
    prompt = _thread_channel_prompt(record, len(broker_code_blocks))
    try:
        existing = str(getattr(event, "channel_prompt", "") or "").strip()
        event.channel_prompt = f"{existing}\n\n{prompt}" if existing else prompt
    except Exception:
        pass


def _thread_source(source: Any, thread_ts: str) -> Any:
    try:
        if dataclasses.is_dataclass(source):
            return dataclasses.replace(source, thread_id=thread_ts)
    except Exception:
        pass
    data: dict[str, Any] = {}
    try:
        data.update(vars(source))
    except TypeError:
        pass
    for key in ("platform", "chat_id", "chat_name", "chat_type", "user_id", "user_name", "guild_id"):
        data.setdefault(key, getattr(source, key, None))
    data["thread_id"] = thread_ts
    return SimpleNamespace(**data)


def _build_thread_event(event: Any, text: str, thread_ts: str, record: dict[str, Any]) -> Any:
    from gateway.platforms.base import MessageEvent, MessageType

    synthetic = MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_thread_source(getattr(event, "source", None), thread_ts),
        raw_message={"team_id": record.get("team_id") or "", "thread_ts": thread_ts},
        message_id=f"hedge-evaluation-deploy:{thread_ts}:initial",
        reply_to_message_id=thread_ts,
    )
    _apply_thread_context(synthetic, record)
    return synthetic


async def _dispatch_initial_request(
    event: Any,
    gateway: Any,
    adapter: Any,
    text: str,
    thread_ts: str,
    record: dict[str, Any],
) -> bool:
    if not text.strip():
        return False
    handler = getattr(adapter, "handle_message", None) or getattr(gateway, "_handle_message", None)
    if handler is None:
        return False
    result = handler(_build_thread_event(event, text, thread_ts, record))
    if asyncio.iscoroutine(result):
        await result
    return True


def _format_thread_prompt(record: dict[str, Any]) -> str:
    requester = _format_user(str(record.get("user_name") or ""), str(record.get("user_id") or ""))
    return "\n".join(
        [
            "请在此 thread 用一个代码块提交完整 broker.json 数组；代码块以外的 JSON 不会用于部署。",
            "可以在代码块外指定 Hedge V2 版本和 ctp_api_test_mode；系统会在部署前展示最终生效值。",
            f"当前发起人：{requester}",
            "也可以直接询问测评环境状态、验证结果或 operation 状态。",
            "系统会先给出脱敏摘要；只有发起人随后回复 `确认部署测评环境` 才会开始部署。",
            "请只在有权限查看这些凭据的私有 Slack 频道中提交。",
        ]
    )


async def _handle_command_start(event: Any, gateway: Any, args: str) -> None:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    adapter = gateway.adapters.get(getattr(source, "platform", None))
    if adapter is None or not chat_id:
        logger.warning("[hedge-evaluation-deploy] Slack adapter/chat missing")
        return

    user_name = await _resolve_user_name(adapter, event)
    record = _base_record(event, user_name)
    root_text = "\n".join(
        [
            "Hedge 测评环境部署线程已开启。",
            f"发起人：{_format_user(record['user_name'], record['user_id'])}",
            "请在此 thread 内完成 broker.json、Hedge V2 版本和 ctp_api_test_mode 收集、脱敏确认、部署和验证。",
        ]
    )
    if args.strip():
        root_text += "\n已安全接收初始资料（敏感内容不回显）。"

    root_response = await _post_slack_message(adapter, chat_id, root_text)
    thread_ts = str(root_response.get("ts") or "")
    if not thread_ts:
        raise RuntimeError("Slack did not return a thread root timestamp")
    record["thread_ts"] = thread_ts
    record["updated_at"] = time.time()

    async with _STATE_LOCK:
        state = await _load_state()
        state.setdefault("threads", {})[_thread_key(event, thread_ts=thread_ts)] = record
        await _save_state(state)

    _remember_slack_thread(adapter, thread_ts)
    _discard_slash_context(adapter, chat_id)

    if args.strip() and await _dispatch_initial_request(event, gateway, adapter, args, thread_ts, record):
        return
    fallback = (
        "已收到初始资料，但当前 gateway 无法自动接入 agent；请在此 thread 再回复一次。"
        if args.strip()
        else _format_thread_prompt(record)
    )
    reply = await _post_slack_message(adapter, chat_id, fallback, thread_ts=thread_ts)
    _remember_slack_thread(adapter, thread_ts, str(reply.get("ts") or ""))


async def _handle_evaluation_event(event: Any, gateway: Any) -> None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return
    args = _command_args(str(getattr(event, "text", "") or ""))
    if args is not None:
        await _handle_command_start(event, gateway, args)


def _schedule_evaluation_event(event: Any, gateway: Any) -> None:
    task = asyncio.create_task(_handle_evaluation_event(event, gateway))

    def _done(done: asyncio.Task) -> None:
        try:
            done.result()
        except Exception as exc:
            logger.warning("[hedge-evaluation-deploy] Slack workflow failed: %s", exc, exc_info=True)

    task.add_done_callback(_done)


def _pre_gateway_dispatch(event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return None

    # Hooks run before normal gateway auth, so never let the workflow bypass it.
    auth = getattr(gateway, "_is_user_authorized", None)
    if auth is not None:
        try:
            if not auth(source):
                return {"action": "allow"}
        except Exception as exc:
            logger.debug("[hedge-evaluation-deploy] Auth check failed: %s", exc)
            return {"action": "allow"}

    text = str(getattr(event, "text", "") or "")
    if _command_args(text) is not None:
        _schedule_evaluation_event(event, gateway)
        return {"action": "skip", "reason": "hedge-evaluation-deploy-command"}

    thread_ts = str(getattr(source, "thread_id", "") or "")
    if not thread_ts:
        return None
    path = _state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        record = (state.get("threads") or {}).get(_thread_key(event))
    except Exception:
        record = None
    if not isinstance(record, dict):
        return None
    _apply_thread_context(event, record)
    return {"action": "allow"}


def _usage(raw_args: str) -> str:
    del raw_args
    return (
        "Usage: `/hedge-evaluation-deploy [状态 | 验证 | operation ID]`\n"
        "部署时会创建专用 thread；请用恰好一个代码块提交 broker.json，并在代码块外按需指定 Hedge V2 版本和 ctp_api_test_mode。系统展示完整脱敏摘要并取得明确确认后才会调用部署 MCP。"
    )


def register(ctx) -> None:
    ctx.register_command(
        COMMAND_NAME,
        _usage,
        description="Start a Hedge evaluation deployment Slack workflow",
        args_hint="[状态 | 验证 | operation ID]",
        platforms=("slack",),
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
