"""Calendar tool — spec §3.3.1: read + create/update/delete events.

Timezone handling (fixes a confirmed 2-3 hour readout shift): Google
Calendar's API requires every dateTime it's given to be unambiguous —
either it carries a UTC offset itself, or the event's timeZone field says
how to interpret it. The old version of this file did neither: it sent
the model's raw string straight through with no timeZone field at all,
so a naive "local time" string from the model (e.g. "2026-09-11T19:00:00",
meaning 7pm Cairo) got silently interpreted as UTC by Google — exactly a
2-3 hour shift depending on Cairo's EET/EEST offset. Every write below
now explicitly anchors to Africa/Cairo, and every read converts Google's
returned (correctly-offset) instant back to Cairo wall-clock before it
ever reaches the model or the user, so "7pm" always means Cairo 7pm on
both ends regardless of what offset the model happens to produce.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from zaki.tools.base import ToolContext, ToolDefinition

_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CALENDAR_ID = "primary"


def _service(context: ToolContext):
    if context.google_credentials is None:
        raise RuntimeError("Google account not connected yet")
    return build("calendar", "v3", credentials=context.google_credentials)


def _parse_as_cairo(value: str) -> datetime:
    """Parses an ISO 8601 string from the model. A naive string (no
    offset) is treated as already being Cairo wall-clock time — the
    contract this tool's own parameter descriptions and prompts.py's
    calendar section both state explicitly. A string that DOES carry an
    offset (in case the model adds one out of habit) is converted to
    Cairo time rather than trusted as-is, so behavior is correct either
    way.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_CAIRO_TZ)
    return dt.astimezone(_CAIRO_TZ)


def _event_datetime_body(value: str) -> dict[str, str]:
    cairo_dt = _parse_as_cairo(value)
    return {"dateTime": cairo_dt.replace(tzinfo=None).isoformat(), "timeZone": "Africa/Cairo"}


def _format_event_start(event: dict[str, Any]) -> str:
    start = event.get("start", {})
    if "dateTime" in start:
        cairo_dt = datetime.fromisoformat(start["dateTime"]).astimezone(_CAIRO_TZ)
        return cairo_dt.strftime("%Y-%m-%d %H:%M")
    return start.get("date", "?")  # all-day event — a bare date, no time/zone to convert


async def list_events(args: dict[str, Any], context: ToolContext) -> str:
    time_min = _parse_as_cairo(args["time_min"]).isoformat() if args.get("time_min") else datetime.now(_CAIRO_TZ).isoformat()
    time_max = _parse_as_cairo(args["time_max"]).isoformat() if args.get("time_max") else None
    service = _service(context)
    result = (
        service.events()
        .list(
            calendarId=_CALENDAR_ID,
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
        return "لا توجد مواعيد في هذه الفترة."
    lines = []
    for e in events:
        start = _format_event_start(e)
        lines.append(f"- [{e['id']}] {e.get('summary', '(بدون عنوان)')} @ {start} (بتوقيت القاهرة)")
    return "\n".join(lines)


async def create_event(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    event: dict[str, Any] = {
        "summary": args["summary"],
        "start": _event_datetime_body(args["start"]),
        "end": _event_datetime_body(args["end"]),
    }
    if args.get("description"):
        event["description"] = args["description"]
    created = service.events().insert(calendarId=_CALENDAR_ID, body=event).execute()
    start_display = _format_event_start(created)
    return f"تم إنشاء الموعد: {created.get('summary')} في {start_display} بتوقيت القاهرة."


async def _find_matching_events(service, query: str, start_date: str | None) -> list[dict[str, Any]]:
    if start_date:
        day_start = _parse_as_cairo(start_date).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        time_min, time_max = day_start.isoformat(), day_end.isoformat()
    else:
        # No date given — search a generous window (a year back, two years
        # forward) rather than the whole calendar's history. timedelta, not
        # .replace(year=...): the latter's window edge sits at today's
        # month/day in the target year, so it can miss an event later in
        # that same year than today's date — confirmed by testing an event
        # one day past that boundary going unfound.
        now = datetime.now(_CAIRO_TZ)
        time_min = (now - timedelta(days=365)).isoformat()
        time_max = (now + timedelta(days=730)).isoformat()

    result = (
        service.events()
        .list(calendarId=_CALENDAR_ID, timeMin=time_min, timeMax=time_max, singleEvents=True, q=query, maxResults=10)
        .execute()
    )
    return result.get("items", [])


async def _resolve_single_event(
    service, event_id_or_summary: str, start_date: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (event, error_message) — event is None if not found or
    ambiguous, in which case error_message explains what to do next.
    """
    try:
        event = service.events().get(calendarId=_CALENDAR_ID, eventId=event_id_or_summary).execute()
        return event, None
    except Exception:
        pass  # not a real event id — fall through to a title search

    matches = await _find_matching_events(service, event_id_or_summary, start_date)
    if not matches:
        return None, f"لم أجد أي موعد بعنوان يطابق \"{event_id_or_summary}\"."
    if len(matches) > 1:
        lines = [f"- [{m['id']}] {m.get('summary', '(بدون عنوان)')} @ {_format_event_start(m)}" for m in matches]
        return None, (
            "وجدت أكثر من موعد مطابق، فاختر أحدها بذكر رقم المعرّف [id] أو حدد "
            "تاريخًا أدق:\n" + "\n".join(lines)
        )
    return matches[0], None


async def delete_event(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    event, error = await _resolve_single_event(service, args["event_id_or_summary"], args.get("start_date"))
    if error:
        return error
    service.events().delete(calendarId=_CALENDAR_ID, eventId=event["id"]).execute()
    return f"تم حذف الموعد: {event.get('summary', '(بدون عنوان)')} @ {_format_event_start(event)}."


async def update_event(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    event, error = await _resolve_single_event(service, args["event_id_or_summary"], args.get("start_date"))
    if error:
        return error

    if args.get("new_summary"):
        event["summary"] = args["new_summary"]
    if args.get("new_start"):
        event["start"] = _event_datetime_body(args["new_start"])
    if args.get("new_end"):
        event["end"] = _event_datetime_body(args["new_end"])
    if not any([args.get("new_summary"), args.get("new_start"), args.get("new_end")]):
        return "لم تحدّد أي تغيير — اذكر العنوان الجديد أو الوقت الجديد."

    updated = service.events().update(calendarId=_CALENDAR_ID, eventId=event["id"], body=event).execute()
    return f"تم تحديث الموعد: {updated.get('summary')} إلى {_format_event_start(updated)} بتوقيت القاهرة."


TOOLS = [
    ToolDefinition(
        name="list_calendar_events",
        description=(
            "List upcoming Google Calendar events in a time range. "
            "time_min/time_max are Cairo LOCAL wall-clock datetimes with no "
            "timezone suffix (e.g. 2026-09-11T00:00:00) — never UTC or a Z "
            "suffix. Returned times are already Cairo local."
        ),
        parameters={
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "description": "Cairo local ISO 8601 datetime, no timezone suffix; defaults to now",
                },
                "time_max": {
                    "type": "string",
                    "description": "Cairo local ISO 8601 datetime, no timezone suffix; optional",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=list_events,
    ),
    ToolDefinition(
        name="create_calendar_event",
        description=(
            "Create a new Google Calendar event. start/end are Cairo LOCAL "
            "wall-clock datetimes with no timezone suffix (e.g. "
            "2026-09-11T19:00:00 for 7pm Cairo time) — never UTC or a Z "
            "suffix, this tool anchors them to Africa/Cairo itself."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "Cairo local ISO 8601 datetime, no timezone suffix"},
                "end": {"type": "string", "description": "Cairo local ISO 8601 datetime, no timezone suffix"},
                "description": {"type": "string"},
            },
            "required": ["summary", "start", "end"],
            "additionalProperties": False,
        },
        handler=create_event,
    ),
    ToolDefinition(
        name="delete_calendar_event",
        description=(
            "Delete a Google Calendar event, permanently and without undo. "
            "Always confirm with the user which specific event before "
            "calling this — read its title and time back to them first. "
            "Pass either a real event id (from list_calendar_events) or a "
            "title to search for; start_date (Cairo local date, "
            "YYYY-MM-DD) narrows the search to that day when given. If "
            "more than one event matches, this returns the candidates "
            "instead of deleting anything — ask the user which one and "
            "call again with its exact event id."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_id_or_summary": {"type": "string", "description": "Event id, or a title to search for"},
                "start_date": {"type": "string", "description": "Cairo local date YYYY-MM-DD, optional"},
            },
            "required": ["event_id_or_summary"],
            "additionalProperties": False,
        },
        handler=delete_event,
    ),
    ToolDefinition(
        name="update_calendar_event",
        description=(
            "Reschedule or edit an existing Google Calendar event. Same "
            "lookup rules as delete_calendar_event (event id or a title "
            "search, optionally narrowed by start_date). Only pass the "
            "fields that are actually changing; new_start/new_end are "
            "Cairo local wall-clock datetimes with no timezone suffix, "
            "same contract as create_calendar_event."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_id_or_summary": {"type": "string", "description": "Event id, or a title to search for"},
                "start_date": {"type": "string", "description": "Cairo local date YYYY-MM-DD, optional"},
                "new_summary": {"type": "string"},
                "new_start": {"type": "string", "description": "Cairo local ISO 8601 datetime, no timezone suffix"},
                "new_end": {"type": "string", "description": "Cairo local ISO 8601 datetime, no timezone suffix"},
            },
            "required": ["event_id_or_summary"],
            "additionalProperties": False,
        },
        handler=update_event,
    ),
]
