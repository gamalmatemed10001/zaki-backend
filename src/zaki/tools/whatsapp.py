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

import re
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


def sanitize_phone_number(raw: str) -> str:
    """Normalizes a phone number to the international, country-code-first
    digit string Evolution API expects (e.g. "201234567890").

    Only handles the one ambiguous case this system actually sees: a local
    Egyptian number typed with the domestic 0-prefix (01XXXXXXXXX, 11
    digits) instead of the international 20-prefix. Anything already
    starting with a country code (or anything else) is left as-is — this
    isn't a general libphonenumber replacement, just enough to stop the
    single mistake a user actually makes when dictating a number.
    """
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0") and len(digits) == 11:
        digits = "20" + digits[1:]
    return digits


def _friendly_send_error(status_code: int, body_text: str) -> str:
    """Turns Evolution API's raw error response into a short, specific
    Arabic message safe to relay to the user — never the raw JSON/HTTP
    text, and never phrased as success.
    """
    import json

    try:
        body = json.loads(body_text)
    except (ValueError, TypeError):
        body = None

    message = None
    if isinstance(body, dict):
        message = (body.get("response") or {}).get("message")

    # Invalid / not-on-WhatsApp number: response.message is a list of
    # per-number results with an "exists" flag — confirmed live against
    # Evolution API for nonexistent, malformed, and local-format numbers.
    if isinstance(message, list):
        numbers = [
            str(item.get("number", "")) for item in message if isinstance(item, dict) and not item.get("exists", True)
        ]
        if numbers:
            return f"فشل الإرسال: الرقم غير صحيح أو غير مسجل على واتساب ({', '.join(numbers)})"

    # Disconnected session: Evolution API returns HTTP 500 with a plain
    # "Connection Closed" string when the WhatsApp instance isn't linked.
    if isinstance(message, str) and "connection closed" in message.lower():
        return "فشل الإرسال: جلسة الواتساب غير متصلة، يلزم إعادة مسح رمز QR"

    if status_code >= 500:
        return f"فشل الإرسال: خطأ في خادم واتساب (كود {status_code})"
    return f"فشل الإرسال: طلب غير صالح (كود {status_code})"


async def send_whatsapp_text(to: str, text: str) -> None:
    """Calls Evolution API directly. Not itself an LLM tool — used by
    send_pending_whatsapp_message below, and by the webhook's auto-reply.
    """
    base_url, api_key, instance = _require_config()
    number = sanitize_phone_number(to)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/message/sendText/{instance}",
            headers={"apikey": api_key},
            json={"number": number, "text": text},
        )
    if resp.status_code >= 400:
        raise RuntimeError(_friendly_send_error(resp.status_code, resp.text))


async def get_whatsapp_connection_status() -> str:
    """Queries Evolution API's connection-state endpoint for this
    instance. Not itself an LLM tool's only caller — used by the
    check_whatsapp_connection tool below and available for /health too.
    Returns a short Arabic status string; raises RuntimeError (never
    silently reports "connected") if the check itself fails.
    """
    base_url, api_key, instance = _require_config()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base_url}/instance/connectionState/{instance}",
            headers={"apikey": api_key},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"تعذّر التحقق من حالة الاتصال (كود {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    state = ((data.get("instance") or {}).get("state") or "").lower()
    if state == "open":
        return "متصل — جلسة الواتساب نشطة."
    if state in ("close", "closed"):
        return "غير متصل — يلزم إعادة مسح رمز QR لإعادة الاتصال."
    if state == "connecting":
        return "قيد الاتصال — الجلسة تحاول إعادة الارتباط الآن."
    return f"حالة غير معروفة من واتساب: {state or 'بدون قيمة'}"


_latest_qr_base64: str | None = None


async def _trigger_whatsapp_connect() -> dict[str, Any]:
    """Calls Evolution API's connect endpoint, which both starts/reconnects
    the instance's WhatsApp session and (when it isn't already paired)
    returns a fresh QR code to scan. Endpoint per Evolution API v2's
    commonly-documented "connect instance" call — verify against your own
    instance's version, same caveat as this module's docstring.
    """
    base_url, api_key, instance = _require_config()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{base_url}/instance/connect/{instance}",
            headers={"apikey": api_key},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"تعذّرت إعادة الاتصال (كود {resp.status_code}): {resp.text[:200]}")
    return resp.json()


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
        # exc's message is already a short, specific Arabic sentence (see
        # _friendly_send_error) — relay it as-is, never as "sent".
        return str(exc)

    del _pending_whatsapp[context.session_id]
    return f"Sent WhatsApp message to {pending.to}."


async def check_whatsapp_connection(args: dict[str, Any], context: ToolContext) -> str:
    try:
        return await get_whatsapp_connection_status()
    except RuntimeError as exc:
        return str(exc)


async def reconnect_whatsapp_session(args: dict[str, Any], context: ToolContext) -> str:
    """Auto-reconnect / status-refresh: checks the instance's actual state
    first (never blindly restarts an already-working session — that would
    needlessly interrupt a live connection), and only calls Evolution
    API's connect endpoint when it's genuinely not open. When that call
    comes back with a fresh QR code, the image is cached in-process and
    served over plain HTTP at GET /api/whatsapp/qr — a base64 data URI
    dumped into a spoken/text chat reply isn't something a user can
    usefully act on, but a link they can open on their phone is.
    """
    global _latest_qr_base64

    try:
        state = await get_whatsapp_connection_status()
    except RuntimeError as exc:
        return str(exc)

    if "متصل" in state and "غير متصل" not in state and "قيد الاتصال" not in state:
        return "الجلسة متصلة بالفعل، لا حاجة لإعادة الاتصال."

    try:
        data = await _trigger_whatsapp_connect()
    except RuntimeError as exc:
        return str(exc)

    base64_qr = data.get("base64")
    if base64_qr:
        _latest_qr_base64 = base64_qr
        settings = get_settings()
        base = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
        qr_url = f"{base}/api/whatsapp/qr"
        return (
            "تم بدء إعادة الاتصال. افتح هذا الرابط على هاتفك وامسح رمز QR "
            f"من واتساب لاستعادة الجلسة فورًا: {qr_url}"
        )

    pairing_code = data.get("pairingCode") or data.get("code")
    if pairing_code:
        return f"تم بدء إعادة الاتصال. أدخل رمز الاقتران التالي في واتساب: {pairing_code}"

    return "تم إرسال طلب إعادة الاتصال، لكن لم يصل رمز QR أو رمز اقتران بعد — تحقّق من الحالة خلال لحظات."


def get_cached_qr_base64() -> str | None:
    """Read by main.py's GET /api/whatsapp/qr — the most recent QR code
    generated by reconnect_whatsapp_session, if any. None means either
    reconnect_whatsapp_session hasn't been called this process lifetime,
    or the last call didn't get back a QR (e.g. already connected).
    """
    return _latest_qr_base64


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
    ToolDefinition(
        name="check_whatsapp_connection",
        description=(
            "Check whether the WhatsApp (Evolution API) session is "
            "currently connected, disconnected (needs QR re-scan), or "
            "connecting. Use this before telling the user WhatsApp is "
            "working, or to diagnose a failed send."
        ),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=check_whatsapp_connection,
        requires_google=False,
    ),
    ToolDefinition(
        name="reconnect_whatsapp_session",
        description=(
            "Reconnect WhatsApp when it's disconnected. Checks the current "
            "state first and does nothing if it's already connected. If "
            "reconnecting requires scanning a QR code, returns a URL the "
            "user can open on their phone to scan it — read that URL out "
            "clearly rather than any raw code data."
        ),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=reconnect_whatsapp_session,
        requires_google=False,
    ),
]
