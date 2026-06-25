"""Forward selected Slack Socket Mode actions to an internal endpoint."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_FORWARD_URL = "http://192.168.139.8:9000/internal/slack/socket-interactions"
DEFAULT_ACTION_IDS = ("create_issue", "create_issue_and_pr")
DEFAULT_TIMEOUT_SECONDS = 10.0

def _csv_set(raw: str | None) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def _configured_action_ids() -> list[str]:
    configured = _csv_set(os.getenv("SLACK_SOCKET_FORWARD_ACTION_IDS"))
    return configured or list(DEFAULT_ACTION_IDS)


def _configured_forward_url() -> str:
    return (os.getenv("SLACK_SOCKET_FORWARD_URL") or DEFAULT_FORWARD_URL).strip()


def _configured_timeout() -> float:
    raw = (os.getenv("SLACK_SOCKET_FORWARD_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _incident_id_from_action(action: dict[str, Any]) -> str:
    raw_value = action.get("value", "")
    if not raw_value:
        return ""
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        return ""
    if isinstance(decoded, dict):
        return str(decoded.get("incident_id", "") or "")
    return ""


def _build_forward_payload(
    body: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, str]:
    message_ts = (
        ((body.get("container") or {}).get("message_ts") or "")
        or ((body.get("message") or {}).get("ts") or "")
    )
    return {
        "action_id": str(action.get("action_id", "") or ""),
        "incident_id": _incident_id_from_action(action),
        "user_id": str((body.get("user") or {}).get("id", "") or ""),
        "channel_id": str((body.get("channel") or {}).get("id", "") or ""),
        "message_ts": str(message_ts or ""),
    }


async def _post_forward_payload(payload: dict[str, str]) -> dict[str, Any]:
    target_url = _configured_forward_url()
    if not target_url:
        logger.warning("[slack-socket-forwarder] No forward URL configured")
        return {"ok": False, "error": "missing_forward_url"}

    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_configured_timeout())
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(target_url, json=payload) as response:
            response_text = await response.text()
            if response.status >= 400:
                raise RuntimeError(
                    f"forward target returned HTTP {response.status}: "
                    f"{response_text[:300]}"
                )
            try:
                return await response.json()
            except Exception:
                return {"ok": True, "text": response_text}


async def _handle_forward_action(ack, body: dict[str, Any], action: dict[str, Any]) -> None:
    await ack()

    payload = _build_forward_payload(body, action)
    try:
        result = await _post_forward_payload(payload)
        logger.info(
            "[slack-socket-forwarder] Forwarded Slack action %s for incident %s: %s",
            payload.get("action_id"),
            payload.get("incident_id"),
            result,
        )
    except Exception as exc:
        logger.warning(
            "[slack-socket-forwarder] Failed to forward Slack action %s: %s",
            payload.get("action_id"),
            exc,
        )


def register(ctx) -> None:
    action_ids = _configured_action_ids()
    if not action_ids:
        logger.warning("[slack-socket-forwarder] No Slack action IDs configured")
        return

    for action_id in action_ids:
        ctx.register_slack_action_handler(action_id, _handle_forward_action)

    logger.info(
        "[slack-socket-forwarder] Registered Slack action forwarders: %s",
        ", ".join(action_ids),
    )
