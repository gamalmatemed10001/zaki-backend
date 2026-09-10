"""Smart reminders — a tool the LLM calls with a resolved ISO 8601
datetime. The model itself parses natural-language time expressions
(e.g. "الساعة 5 مساءً", "خلال ساعة") into an absolute datetime, using the
current date/time pipeline.py injects into the system prompt every turn.

Delivery is always via Telegram (text, then a best-effort edge-tts voice
note) — see zaki/scheduler.py's docstring for why. No confirmation gate
here (unlike gmail.py/whatsapp.py's send tools): a reminder only reaches
the one allowlisted Telegram user this whole system is built around, not
a third party.

In-memory scheduling — a reminder set now is lost if the backend restarts
before it fires. Flagged here rather than silently assumed durable; see
zaki/scheduler.py for the tradeoff.
"""

from datetime import datetime
from typing import Any

from zaki.config import get_settings
from zaki.scheduler import schedule_reminder
from zaki.tools.base import ToolContext, ToolDefinition


async def create_reminder(args: dict[str, Any], context: ToolContext) -> str:
    settings = get_settings()
    if not settings.telegram_allowed_user_id:
        return "Error: Telegram isn't configured, so there's no chat to deliver a reminder to."

    try:
        remind_at = datetime.fromisoformat(args["remind_at"])
    except ValueError:
        return "Error: remind_at must be a valid ISO 8601 datetime, e.g. 2026-09-10T17:00:00"

    now = datetime.now(remind_at.tzinfo) if remind_at.tzinfo else datetime.now()
    if remind_at <= now:
        return (
            "Error: that time is already in the past relative to now. Ask "
            "the user to confirm whether they mean tomorrow or a "
            "different time, then call this again with the corrected time."
        )

    schedule_reminder(
        chat_id=int(settings.telegram_allowed_user_id),
        remind_at=remind_at,
        message=args["message"],
    )
    return f"Reminder scheduled for {remind_at.isoformat(timespec='minutes')}: {args['message']}"


TOOLS = [
    ToolDefinition(
        name="create_reminder",
        description=(
            "Schedule a reminder to be pushed to the user via Telegram "
            "(text and a spoken voice note) at a specific future time. "
            "Resolve any relative/natural time expression (e.g. 'in an "
            "hour', 'at 5pm today') into an absolute ISO 8601 datetime "
            "yourself, using the current date and time given in your "
            "system context — this tool does not parse natural language."
        ),
        parameters={
            "type": "object",
            "properties": {
                "remind_at": {
                    "type": "string",
                    "description": "Absolute ISO 8601 datetime, e.g. 2026-09-10T17:00:00",
                },
                "message": {"type": "string", "description": "What to remind the user about"},
            },
            "required": ["remind_at", "message"],
            "additionalProperties": False,
        },
        handler=create_reminder,
        requires_google=False,
    ),
]
