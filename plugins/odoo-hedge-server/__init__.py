"""Slack workflow for Odoo Hedge Server operations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
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
DEFAULT_API_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_API_TIMEOUT_SECONDS = 30.0

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
_TAG_RE = re.compile(r"(?<![\w.])(?P<tag>\d+\.\d+\.\d+)(?![\w.])")
_COMMIT_RE = re.compile(r"(?<![A-Fa-f0-9])(?P<commit>[A-Fa-f0-9]{7,40})(?![A-Fa-f0-9])")
_BRANCH_FIELD_RE = re.compile(
    r"(?:branch|分支)\s*[:=：]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{0,127})",
    re.IGNORECASE,
)
_COMMIT_FIELD_RE = re.compile(
    r"(?:commit|提交)\s*[:=：]?\s*(?P<value>[A-Fa-f0-9]{7,40})",
    re.IGNORECASE,
)
_TAG_FIELD_RE = re.compile(
    r"(?:tag|标签)\s*[:=：]?\s*(?P<value>\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_SLUG_FIELD_RE = re.compile(
    r"(?:slug|sandbox|沙盒|实例|服务)\s*[:=：]\s*(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]{1,127})",
    re.IGNORECASE,
)
_SLUG_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


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


async def _api_request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    return await asyncio.to_thread(_api_request_sync, method, path, body)


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


def _match_value(pattern: re.Pattern[str], text: str, group: str = "value") -> str:
    match = pattern.search(text or "")
    return str(match.group(group) or "").strip() if match else ""


def _parse_tag(text: str) -> str:
    explicit = _match_value(_TAG_FIELD_RE, text)
    if explicit:
        return explicit
    return _match_value(_TAG_RE, text, "tag")


def _parse_commit(text: str) -> str:
    explicit = _match_value(_COMMIT_FIELD_RE, text)
    if explicit:
        return explicit
    return _match_value(_COMMIT_RE, text, "commit")


def _parse_branch(text: str, version: str) -> str:
    explicit = _match_value(_BRANCH_FIELD_RE, text)
    if explicit:
        return explicit
    return version


def _parse_slug(text: str, intent: str) -> str:
    explicit = _match_value(_SLUG_FIELD_RE, text)
    if explicit:
        return explicit
    if intent == "create":
        return ""
    ignored = {
        "delete",
        "destroy",
        "remove",
        "status",
        "list",
        "get",
        "upgrade",
        "update",
        "sandbox",
    }
    for raw_token in re.split(r"[\s,，。:：]+", text or ""):
        token = raw_token.strip("`'\"“”‘’()[]{}<>")
        if not token:
            continue
        lowered = token.lower()
        if lowered in ignored:
            continue
        if any(word in token for word in ("销毁", "删除", "关闭", "停止", "状态", "列状态", "查看", "升级", "更新", "沙盒", "实例", "服务")):
            continue
        if _SLUG_TOKEN_RE.fullmatch(token):
            return token
    return ""


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
    intent = _parse_intent(text)
    version = _parse_version(text)
    tag = _parse_tag(text)
    commit = "" if tag else _parse_commit(text)
    branch = "" if tag else _parse_branch(text, version)
    return {
        "intent": intent,
        "version": version,
        "branch": branch,
        "commit": commit,
        "tag": tag,
        "slug": _parse_slug(text, intent),
        "raw_text": (text or "").strip(),
    }


def _has_create_target(record: dict[str, Any]) -> bool:
    return any(str(record.get(key) or "").strip() for key in ("branch", "commit", "tag"))


def _has_actionable_fields(parsed: dict[str, str]) -> bool:
    return any(parsed.get(key) for key in ("branch", "commit", "tag", "slug", "version"))


def _format_user(user_name: str, user_id: str) -> str:
    if user_name and user_id and user_name != user_id:
        return f"{user_name} (`{user_id}`)"
    return user_id or user_name or "unknown"


def _format_sandbox_record(record: dict[str, Any]) -> str:
    lines: list[str] = []
    for label, key in (
        ("slug", "slug"),
        ("状态", "status"),
        ("URL", "url"),
        ("owner", "owner"),
        ("branch", "branch"),
        ("commit", "commit"),
        ("tag", "tag"),
        ("image", "image_tag"),
        ("sync defaults", "sync_defaults_status"),
    ):
        value = record.get(key)
        if value is None or value == "":
            continue
        if key in {"slug", "commit", "tag", "branch", "image_tag"}:
            lines.append(f"- {label}：`{value}`")
        else:
            lines.append(f"- {label}：{value}")
    last_error = str(record.get("last_error") or "").strip()
    if last_error:
        lines.append(f"- last_error：{last_error}")
    sync_error = str(record.get("sync_defaults_error") or "").strip()
    if sync_error:
        lines.append(f"- sync_defaults_error：{sync_error}")
    return "\n".join(lines) if lines else "- 返回：无详情"


def _format_sandbox_list(records: Any) -> str:
    if not isinstance(records, list):
        return "HTTP 服务返回了非预期的状态列表格式。"
    if not records:
        return "当前没有 sandbox。"
    lines = ["当前 sandbox："]
    for item in records[:10]:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "unknown")
        status = str(item.get("status") or "unknown")
        url = str(item.get("url") or "")
        owner = str(item.get("owner") or "")
        suffix = f" - {url}" if url else ""
        owner_text = f" ({owner})" if owner else ""
        lines.append(f"- `{slug}`：{status}{owner_text}{suffix}")
    if len(records) > 10:
        lines.append(f"... 还有 {len(records) - 10} 个未显示")
    return "\n".join(lines)


def _format_target_prompt(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            "已创建 Odoo Hedge Server 操作线程。",
            f"用户：{_format_user(str(record.get('user_name') or ''), str(record.get('user_id') or ''))}",
            f"我理解你的意图是：{_INTENT_LABELS.get(str(record.get('intent') or 'unknown'), '未识别')}。",
            "请在此 thread 回复要部署的 branch、commit 或 tag，例如 `17.0`、`abcdef1` 或 `2026.6.1`。",
        ]
    )


def _format_slug_prompt(record: dict[str, Any]) -> str:
    return "\n".join(
        [
            "已创建 Odoo Hedge Server 操作线程。",
            f"用户：{_format_user(str(record.get('user_name') or ''), str(record.get('user_id') or ''))}",
            f"我理解你的意图是：{_INTENT_LABELS.get(str(record.get('intent') or 'unknown'), '未识别')}。",
            "请在此 thread 回复 sandbox slug，例如 `odoo-demo-123`。",
        ]
    )


def _format_unknown_prompt() -> str:
    return "请说明要执行的操作：创建、销毁或列状态。创建时可提供 branch/commit/tag；销毁时请提供 sandbox slug。"


def _format_api_error(exc: SandboxApiError) -> str:
    parts = []
    if exc.status is not None:
        parts.append(str(exc.status))
    if exc.code:
        parts.append(exc.code)
    suffix = f"（{' '.join(parts)}）" if parts else ""
    return f"HTTP 服务调用失败{suffix}：{exc.message}"


def _owner_for_api(record: dict[str, Any]) -> str:
    return str(record.get("user_name") or record.get("user_id") or "").strip()


def _create_body(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    owner = _owner_for_api(record)
    if owner:
        body["owner"] = owner
    for key in ("branch", "commit", "tag"):
        value = str(record.get(key) or "").strip()
        if value:
            body[key] = value
    return body


async def _execute_record(record: dict[str, Any]) -> str:
    intent = str(record.get("intent") or "unknown")
    record["pending"] = ""
    try:
        if intent == "create":
            if not _has_create_target(record):
                record["pending"] = "create_target"
                return _format_target_prompt(record)
            result = await _api_request("POST", "/sandboxes", _create_body(record))
            if isinstance(result, dict):
                record["slug"] = str(result.get("slug") or record.get("slug") or "")
                return "已创建 Odoo sandbox：\n" + _format_sandbox_record(result)
            return "已创建 Odoo sandbox，但 HTTP 服务返回了非预期格式。"

        if intent == "destroy":
            slug = str(record.get("slug") or "").strip()
            if not slug:
                record["pending"] = "slug"
                return _format_slug_prompt(record)
            result = await _api_request("POST", f"/sandboxes/{quote(slug, safe='')}/destroy")
            if isinstance(result, dict):
                return "已调用销毁接口：\n" + _format_sandbox_record(result)
            return f"已调用销毁接口：`{slug}`"

        if intent == "status":
            slug = str(record.get("slug") or "").strip()
            if slug:
                result = await _api_request("GET", f"/sandboxes/{quote(slug, safe='')}")
                if isinstance(result, dict):
                    return "Sandbox 状态：\n" + _format_sandbox_record(result)
                return "HTTP 服务返回了非预期的状态格式。"
            return _format_sandbox_list(await _api_request("GET", "/sandboxes"))

        if intent == "upgrade":
            return (
                "当前 HTTP API 未暴露升级接口；OpenAPI 里可用的是创建、销毁、列状态、"
                "查询单个 sandbox 和 provision-sync-defaults。"
            )

        return _format_unknown_prompt()
    except SandboxApiError as exc:
        return _format_api_error(exc)


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
        "branch": parsed.get("branch", ""),
        "commit": parsed.get("commit", ""),
        "tag": parsed.get("tag", ""),
        "slug": parsed.get("slug", ""),
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

    reply = await _execute_record(record)
    record["updated_at"] = time.time()
    async with _STATE_LOCK:
        state = await _load_state()
        state.setdefault("threads", {})[key] = record
        await _save_state(state)

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
    explicit_intent = parsed["intent"] != "unknown"
    pending = str(record.get("pending") or "")
    actionable = _has_actionable_fields(parsed)
    if not explicit_intent and not actionable and not pending:
        updated = dict(record)
        updated["updated_at"] = time.time()
        async with _STATE_LOCK:
            state = await _load_state()
            state.setdefault("threads", {})[key] = updated
            await _save_state(state)
        response = await _post_slack_message(adapter, chat_id, _format_unknown_prompt(), thread_ts=thread_ts)
        _remember_slack_thread(adapter, thread_ts, str(response.get("ts") or ""))
        return

    carry_previous = not explicit_intent
    if not explicit_intent:
        parsed["intent"] = str(record.get("intent") or "unknown")
    if carry_previous:
        for field in ("version", "branch", "commit", "tag", "slug"):
            if not parsed[field]:
                parsed[field] = str(record.get(field) or "")

    user_name = await _resolve_user_name(adapter, event)
    updated = dict(record)
    updated.update(
        {
            "user_id": str(getattr(source, "user_id", "") or record.get("user_id") or ""),
            "user_name": user_name or str(record.get("user_name") or ""),
            "intent": parsed["intent"],
            "version": parsed["version"],
            "branch": parsed["branch"],
            "commit": parsed["commit"],
            "tag": parsed["tag"],
            "slug": parsed["slug"],
            "raw_text": text or str(record.get("raw_text") or ""),
            "updated_at": time.time(),
        }
    )

    reply = await _execute_record(updated)
    updated["updated_at"] = time.time()

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
        "Usage: `/odoo-hedge-server 创建 abcdef1` 或 `/odoo-hedge-server 创建 2026.6.1`\n"
        "Slack 中会创建一个专用 thread，后续直接在该 thread 回复 branch、commit、tag 或 slug。"
    )


def register(ctx) -> None:
    ctx.register_command(
        COMMAND_NAME,
        _usage,
        description="Start an Odoo Hedge Server Slack workflow",
        args_hint="<创建|销毁|升级|状态> [branch|commit|tag|slug]",
        platforms=("slack",),
    )
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
