"""WhatsApp messaging via Evolution API (a self-hosted WhatsApp gateway).

Evolution API is a fast-moving, self-hosted project — its REST surface has
changed across releases (same staleness risk already flagged in this
codebase for ddgs/edge-tts/openWakeWord). The endpoint path and payload
shape below match the commonly-documented v2 "send plain text" call and
"messages.upsert" webhook event at build time; verify both against your
own instance's actual version before relying on this.

Sending mirrors gmail.py's structural confirmation gate: reaching a real
person is exactly the kind of action a prompt-only "ask before sending"
can't safely gate on its own, so draft and send are enforced as two
separate turns here too — see gmail.py's module docstring for the full
rationale (send_pending_whatsapp_message only succeeds if the draft was
staged on a *previous* turn_id, the same structural check used there).
"""

from dataclasses import dataclass
from typing import Any

import httpx

from zaki.config import get_settings
from zaki.tools.base import ToolContext, ToolDefinition


def _require_config() -> tuple[str, str, str]:
    settings = get_settings()
    if not (
        settings.evolution_api_url
        and settings.evolution_api_key
        and settings.evolution_instance_name
    ):
        raise RuntimeError(
            "WhatsApp isn't configured — set EVOLUTION_API_URL, EVOLUTION_API_KEY, "
            "and EVOLUTION_INSTANCE_NAME"
        )
    return (
        settings.evolution_api_url.rstrip("/"),
        settings.evolution_api_key.get_secret_value(),
        settings.evolution_instance_name,
    )


async def send_whatsapp_text(to: str, text: str) -> None:
    """Calls Evolution API directly. Not itself an LLM tool — used by
    send_pending_whatsapp_message below, and by the webhook's auto-reply.
    """
    base_url, api_key, instance = _require_config()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/message/sendText/{instance}",
            headers={"apikey": api_key},
            json={"number": to, "text": text},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Evolution API error {resp.status_code}: {resp.text[:300]}")


@dataclass
class PendingWhatsApp:
    to: str
    message: str
    created_turn_id: str


# Module-level, per-session — same in-memory staging pattern as gmail.py's
# _pending_drafts. A real store (Postgres) takes over if this needs to
# survive process restarts.
_pending_whatsapp: dict[str, PendingWhatsApp] = {}


async def draft_whatsapp_message(args: dict[str, Any], context: ToolContext) -> str:
    _pending_whatsapp[context.session_id] = PendingWhatsApp(
        to=args["recipient_phone"],
        message=args["message"],
        created_turn_id=context.turn_id,
    )
    return (
        f"Draft staged (NOT sent). To: {args['recipient_phone']}\n"
        f"Message: {args['message']}\n"
        "Read this back to the user in full and wait for their explicit "
        "confirmation on their NEXT message before ever calling "
        "send_pending_whatsapp_message — do not call it in this same turn; "
        "it will be rejected if you do."
    )


async def send_pending_whatsapp_message(args: dict[str, Any], context: ToolContext) -> str:
    pending = _pending_whatsapp.get(context.session_id)
    if pending is None:
        return (
            "Error: no pending WhatsApp draft for this session. Draft one "
            "first with draft_whatsapp_message."
        )

    if pending.created_turn_id == context.turn_id:
        return (
            "Error: this draft was staged earlier in the current turn — no "
            "human has confirmed it yet. Read the draft back to the user "
            "and STOP. Only call send_pending_whatsapp_message again after "
            "their next message explicitly confirms."
        )

    try:
        await send_whatsapp_text(pending.to, pending.message)
    except RuntimeError as exc:
        return f"Error sending WhatsApp message: {exc}"

    del _pending_whatsapp[context.session_id]
    return f"Sent WhatsApp message to {pending.to}."


def parse_evolution_webhook(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Extracts (from_number, text) from an Evolution API inbound-message
    webhook payload, or None if this event isn't a plain incoming text
    message (a status update, a group message, media, an echo of our own
    outbound send, etc). Field names match the commonly-documented
    "messages.upsert" event shape — verify against your instance.
    """
    if payload.get("event") != "messages.upsert":
        return None
    data = payload.get("data") or {}
    key = data.get("key") or {}
    if key.get("fromMe"):
        return None
    remote_jid = key.get("remoteJid", "")
    from_number = remote_jid.split("@")[0]
    text = (data.get("message") or {}).get("conversation")
    if not from_number or not text:
        return None
    return from_number, text


TOOLS = [
    ToolDefinition(
        name="draft_whatsapp_message",
        description=(
            "Stage a WhatsApp message draft to a phone number. Does NOT "
            "send. Always read the draft back to the user in full and wait "
            "for their next message to explicitly confirm before ever "
            "calling send_pending_whatsapp_message."
        ),
        parameters={
            "type": "object",
            "properties": {
                "recipient_phone": {
                    "type": "string",
                    "description": "Phone number with country code, e.g. 201234567890",
                },
                "message": {"type": "string"},
            },
            "required": ["recipient_phone", "message"],
            "additionalProperties": False,
        },
        handler=draft_whatsapp_message,
        requires_google=False,
    ),
    ToolDefinition(
        name="send_pending_whatsapp_message",
        description=(
            "Send the most recently staged WhatsApp draft for this "
            "session. Only call this after the user has explicitly "
            "confirmed, in their own separate message, that they want it "
            "sent. Calling it in the same turn as draft_whatsapp_message "
            "will be rejected."
        ),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=send_pending_whatsapp_message,
        requires_google=False,
    ),
]
