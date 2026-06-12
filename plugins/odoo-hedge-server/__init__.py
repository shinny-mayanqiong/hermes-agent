"""Slack workflow for Odoo Hedge Server operations."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from hermes_constants import get_hermes_home
from utils import atomic_replace

logger = logging.getLogger(__name__)

COMMAND_NAME = "odoo-hedge-server"
COMMAND_PREFIX = f"/{COMMAND_NAME}"
ALT_COMMAND_PREFIX = f"!{COMMAND_NAME}"
SKILL_NAME = "odoo-hedge-server"
TOOLSET_NAME = "odoo_hedge_server"
DEFAULT_API_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_API_TIMEOUT_SECONDS = 1200.0

_STATE_LOCK = asyncio.Lock()


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


class SandboxApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


def _api_settings() -> tuple[str, float]:
    base_url = DEFAULT_API_BASE_URL
    timeout = DEFAULT_API_TIMEOUT_SECONDS
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config()
        configured_base = cfg_get(cfg, "odoo_hedge_server", "api_base_url", default=None)
        if configured_base is None:
            configured_base = cfg_get(cfg, "odoo-hedge-server", "api_base_url", default=None)
        if isinstance(configured_base, str) and configured_base.strip():
            base_url = configured_base.strip()

        configured_timeout = cfg_get(cfg, "odoo_hedge_server", "timeout_seconds", default=None)
        if configured_timeout is None:
            configured_timeout = cfg_get(cfg, "odoo-hedge-server", "timeout_seconds", default=None)
        if configured_timeout is not None:
            timeout = float(configured_timeout)
    except Exception as exc:
        logger.debug("[odoo-hedge-server] Failed to load API config: %s", exc)
    if timeout <= 0:
        timeout = DEFAULT_API_TIMEOUT_SECONDS
    return base_url.rstrip("/"), timeout


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _api_error_from_payload(status: int | None, payload: Any, fallback: str) -> SandboxApiError:
    code = ""
    details = None
    message = fallback
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            details = error.get("details")
            message = str(error.get("message") or fallback)
        elif payload.get("detail"):
            message = str(payload.get("detail"))
    elif isinstance(payload, str) and payload.strip():
        message = payload.strip()
    return SandboxApiError(message, status=status, code=code, details=details)


def _api_request_sync(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    base_url, timeout = _api_settings()
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{base_url}/{path.lstrip('/')}",
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return _decode_json(response.read())
    except HTTPError as exc:
        payload = _decode_json(exc.read())
        raise _api_error_from_payload(exc.code, payload, str(exc.reason or "HTTP error")) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise SandboxApiError(f"无法连接 HTTP 服务：{reason}") from exc
    except TimeoutError as exc:
        raise SandboxApiError("HTTP 服务调用超时") from exc
    except OSError as exc:
        raise SandboxApiError(f"HTTP 服务调用失败：{exc}") from exc


def _tool_payload(ok: bool, **fields: Any) -> str:
    payload = {"ok": ok}
    payload.update(fields)
    return json.dumps(payload, ensure_ascii=False)


def _tool_error(exc: SandboxApiError) -> str:
    return _tool_payload(
        False,
        status=exc.status,
        code=exc.code,
        message=exc.message,
        error={
            "status": exc.status,
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
        details=exc.details,
    )


def _clean_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return str(value).strip() if value is not None else ""


def _odoo_sandbox_create_tool(args: dict[str, Any], **_: Any) -> str:
    body = _create_body(args)
    try:
        data = _api_request_sync("POST", "/sandboxes", body)
    except SandboxApiError as exc:
        return _tool_error(exc)
    return _tool_payload(True, status="created", data=data)


def _odoo_sandbox_provision_sync_defaults_tool(args: dict[str, Any], **_: Any) -> str:
    slug = _clean_arg(args, "slug")
    if not slug:
        return _tool_payload(False, message="Missing required slug", error={"code": "missing_slug"})
    try:
        data = _api_request_sync("POST", f"/sandboxes/{quote(slug, safe='')}/provision-sync-defaults")
    except SandboxApiError as exc:
        return _tool_error(exc)
    return _tool_payload(True, status="provisioned", data=data)


def _odoo_sandbox_list_tool(args: dict[str, Any], **_: Any) -> str:
    del args
    try:
        data = _api_request_sync("GET", "/sandboxes")
    except SandboxApiError as exc:
        return _tool_error(exc)
    return _tool_payload(True, status="listed", data=data)


def _odoo_sandbox_get_tool(args: dict[str, Any], **_: Any) -> str:
    slug = _clean_arg(args, "slug")
    if not slug:
        return _tool_payload(False, message="Missing required slug", error={"code": "missing_slug"})
    try:
        data = _api_request_sync("GET", f"/sandboxes/{quote(slug, safe='')}")
    except SandboxApiError as exc:
        return _tool_error(exc)
    return _tool_payload(True, status="found", data=data)


def _odoo_sandbox_destroy_tool(args: dict[str, Any], **_: Any) -> str:
    slug = _clean_arg(args, "slug")
    if not slug:
        return _tool_payload(False, message="Missing required slug", error={"code": "missing_slug"})
    try:
        data = _api_request_sync("POST", f"/sandboxes/{quote(slug, safe='')}/destroy")
    except SandboxApiError as exc:
        return _tool_error(exc)
    return _tool_payload(True, status="destroyed", data=data)


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


def _format_user(user_name: str, user_id: str) -> str:
    if user_name and user_id and user_name != user_id:
        return f"{user_name} (`{user_id}`)"
    return user_id or user_name or "unknown"


def _owner_for_api(record: dict[str, Any]) -> str:
    return str(record.get("owner") or record.get("user_name") or record.get("user_id") or "").strip()


def _create_body(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    owner = _owner_for_api(record)
    if owner:
        body["owner"] = owner
    slug = str(record.get("slug") or "").strip()
    if slug:
        body["slug"] = slug
    if str(record.get("use_default") or "").strip():
        return body
    for key in ("branch", "commit", "tag"):
        value = str(record.get(key) or "").strip()
        if value:
            body[key] = value
    return body


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


def _base_record(event: Any, user_name: str, raw_text: str) -> dict[str, Any]:
    source = getattr(event, "source", None)
    return {
        "team_id": _team_id(event),
        "channel_id": str(getattr(source, "chat_id", "") or ""),
        "thread_ts": str(getattr(source, "thread_id", "") or ""),
        "user_id": str(getattr(source, "user_id", "") or ""),
        "user_name": user_name,
        "raw_text": (raw_text or "").strip(),
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def _merge_auto_skill(existing: Any) -> str | list[str]:
    if not existing:
        return SKILL_NAME
    names = [existing] if isinstance(existing, str) else list(existing)
    deduped: list[str] = []
    for name in [*names, SKILL_NAME]:
        if isinstance(name, str) and name and name not in deduped:
            deduped.append(name)
    return deduped[0] if len(deduped) == 1 else deduped


def _thread_channel_prompt(record: dict[str, Any]) -> str:
    user_name = str(record.get("user_name") or "")
    user_id = str(record.get("user_id") or "")
    return "\n".join(
        [
            "[Odoo Hedge Server Slack thread]",
            "This thread was opened by /odoo-hedge-server. Treat messages here as Odoo sandbox lifecycle requests.",
            f"Slack requester: {_format_user(user_name, user_id)}. Use this requester as the create owner unless the user corrects it.",
            "Users do not need to repeat /odoo-hedge-server inside this thread.",
        ]
    )


def _apply_odoo_thread_context(event: Any, record: dict[str, Any]) -> None:
    try:
        event.auto_skill = _merge_auto_skill(getattr(event, "auto_skill", None))
    except Exception:
        pass
    prompt = _thread_channel_prompt(record)
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
    for key in (
        "platform",
        "chat_id",
        "chat_name",
        "chat_type",
        "user_id",
        "user_name",
        "guild_id",
    ):
        if key not in data:
            data[key] = getattr(source, key, None)
    data["thread_id"] = thread_ts
    return SimpleNamespace(**data)


def _build_thread_event(event: Any, text: str, thread_ts: str, record: dict[str, Any]) -> Any:
    from gateway.platforms.base import MessageEvent, MessageType

    raw = dict(_raw_dict(event))
    raw["text"] = text
    raw["thread_ts"] = thread_ts
    raw.setdefault("team_id", record.get("team_id") or "")
    synthetic = MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_thread_source(getattr(event, "source", None), thread_ts),
        raw_message=raw,
        message_id=f"odoo-hedge-server:{thread_ts}:initial",
        reply_to_message_id=thread_ts,
    )
    _apply_odoo_thread_context(synthetic, record)
    return synthetic


async def _dispatch_thread_event(event: Any, gateway: Any, adapter: Any, thread_ts: str, record: dict[str, Any]) -> bool:
    text = str(record.get("raw_text") or "").strip()
    if not text:
        return False
    synthetic = _build_thread_event(event, text, thread_ts, record)
    handler = getattr(adapter, "handle_message", None) or getattr(gateway, "_handle_message", None)
    if handler is None:
        return False
    result = handler(synthetic)
    if asyncio.iscoroutine(result):
        await result
    return True


def _format_thread_prompt(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            "请直接在此 thread 描述要创建、销毁或查询的 Odoo 服务。",
            f"当前用户：{_format_user(str(record.get('user_name') or ''), str(record.get('user_id') or ''))}",
            "创建示例：`创建默认值`、`创建 2026.6.1`、`创建 abcdef123 slug demo-a`。",
        ]
    )


async def _handle_command_start(event: Any, gateway: Any, args: str) -> None:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    adapter = gateway.adapters.get(getattr(source, "platform", None))
    if adapter is None or not chat_id:
        logger.warning("[odoo-hedge-server] Slack adapter/chat missing for command start")
        return

    user_name = await _resolve_user_name(adapter, event)
    record = _base_record(event, user_name, args)

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

    key = _thread_key(event, thread_ts=thread_ts)
    async with _STATE_LOCK:
        state = await _load_state()
        record["workflow"] = "agent"
        state.setdefault("threads", {})[key] = record
        await _save_state(state)

    _remember_slack_thread(adapter, thread_ts)
    await _replace_slash_ack(
        adapter,
        chat_id,
        "已创建 Odoo Hedge Server thread，请在新 thread 中继续。",
    )

    if args.strip():
        dispatched = await _dispatch_thread_event(event, gateway, adapter, thread_ts, record)
        if dispatched:
            return
        fallback = "已记录初始请求，但当前 gateway 无法自动接入 agent；请在此 thread 再回复一次需求。"
    else:
        fallback = _format_thread_prompt(record)

    reply_response = await _post_slack_message(adapter, chat_id, fallback, thread_ts=thread_ts)
    _remember_slack_thread(adapter, thread_ts, str(reply_response.get("ts") or ""))


async def _handle_odoo_event(event: Any, gateway: Any) -> None:
    source = getattr(event, "source", None)
    if _platform_value(source) != "slack":
        return

    text = str(getattr(event, "text", "") or "")
    args = _command_args(text)
    if args is not None:
        await _handle_command_start(event, gateway, args)
        return


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

    # pre_gateway_dispatch is synchronous, so read the small marker file
    # directly. The actual conversation state lives in the gateway session.
    path = _state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        record = (state.get("threads") or {}).get(_thread_key(event))
        active = isinstance(record, dict)
    except Exception:
        record = None
        active = False
    if not active:
        return None
    _apply_odoo_thread_context(event, record or {})
    return {"action": "allow"}


def _usage(raw_args: str) -> str:
    del raw_args
    return (
        "Usage: `/odoo-hedge-server 创建 默认值` 或 `/odoo-hedge-server 创建 2026.6.1`\n"
        "Slack 中会创建一个专用 thread，后续直接在该 thread 回复版本、commit、tag、默认值或 slug。"
    )


_CREATE_SCHEMA = {
    "name": "odoo_sandbox_create",
    "description": "Create an Odoo sandbox through the Hedge Sandbox Control API.",
    "parameters": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Slack requester name or user id."},
            "slug": {"type": "string", "description": "Optional custom sandbox slug."},
            "branch": {"type": "string", "description": "Source branch or version branch."},
            "commit": {"type": "string", "description": "Source commit SHA."},
            "tag": {"type": "string", "description": "Official release tag in year.month.release_count form."},
        },
        "additionalProperties": False,
    },
}

_PROVISION_SCHEMA = {
    "name": "odoo_sandbox_provision_sync_defaults",
    "description": "Provision default Xinyi account settings for an Odoo sandbox.",
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Sandbox slug returned by create or supplied by the user."},
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
}

_LIST_SCHEMA = {
    "name": "odoo_sandbox_list",
    "description": "List Odoo sandboxes.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

_GET_SCHEMA = {
    "name": "odoo_sandbox_get",
    "description": "Get one Odoo sandbox by slug.",
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Sandbox slug."},
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
}

_DESTROY_SCHEMA = {
    "name": "odoo_sandbox_destroy",
    "description": "Destroy one Odoo sandbox by slug.",
    "parameters": {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Sandbox slug to destroy."},
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
}

_TOOLS = (
    ("odoo_sandbox_create", _CREATE_SCHEMA, _odoo_sandbox_create_tool, "🧱"),
    ("odoo_sandbox_provision_sync_defaults", _PROVISION_SCHEMA, _odoo_sandbox_provision_sync_defaults_tool, "🔐"),
    ("odoo_sandbox_list", _LIST_SCHEMA, _odoo_sandbox_list_tool, "📋"),
    ("odoo_sandbox_get", _GET_SCHEMA, _odoo_sandbox_get_tool, "🔎"),
    ("odoo_sandbox_destroy", _DESTROY_SCHEMA, _odoo_sandbox_destroy_tool, "🗑️"),
)


def register(ctx) -> None:
    ctx.register_command(
        COMMAND_NAME,
        _usage,
        description="Start an Odoo Hedge Server Slack workflow",
        args_hint="<创建|销毁|升级|状态> [版本|commit|tag|默认值|slug]",
        platforms=("slack",),
    )
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET_NAME,
            schema=schema,
            handler=handler,
            emoji=emoji,
        )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
