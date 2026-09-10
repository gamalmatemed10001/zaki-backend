"""Calendar tool — spec §3.3.1: read + create/update events."""

from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build

from zaki.tools.base import ToolContext, ToolDefinition


def _service(context: ToolContext):
    if context.google_credentials is None:
        raise RuntimeError("Google account not connected yet")
    return build("calendar", "v3", credentials=context.google_credentials)


async def list_events(args: dict[str, Any], context: ToolContext) -> str:
    time_min = args.get("time_min") or datetime.now(timezone.utc).isoformat()
    time_max = args.get("time_max")
    service = _service(context)
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        )
        .execute()
    )
    events = result.get("items", [])
    if not events:
        return "No events found in this range."
    lines = []
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date"))
        lines.append(f"- {e.get('summary', '(no title)')} @ {start}")
    return "\n".join(lines)


async def create_event(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    event: dict[str, Any] = {
        "summary": args["summary"],
        "start": {"dateTime": args["start"]},
        "end": {"dateTime": args["end"]},
    }
    if args.get("description"):
        event["description"] = args["description"]
    created = service.events().insert(calendarId="primary", body=event).execute()
    return f"Created: {created.get('summary')} ({created.get('htmlLink')})"


TOOLS = [
    ToolDefinition(
        name="list_calendar_events",
        description=(
            "List upcoming Google Calendar events in a time range. Times are "
            "ISO 8601 (e.g. 2026-09-07T00:00:00Z)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "ISO 8601 start; defaults to now"},
                "time_max": {"type": "string", "description": "ISO 8601 end; optional"},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=list_events,
    ),
    ToolDefinition(
        name="create_calendar_event",
        description="Create a new Google Calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 datetime"},
                "end": {"type": "string", "description": "ISO 8601 datetime"},
                "description": {"type": "string"},
            },
            "required": ["summary", "start", "end"],
            "additionalProperties": False,
        },
        handler=create_event,
    ),
]
