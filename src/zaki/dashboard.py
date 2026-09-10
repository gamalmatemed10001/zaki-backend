"""Read-only aggregation for the web frontend's dashboard widgets.

Deliberately separate from tools/*.py: those return LLM-facing prose
(markdown-free strings meant to be summarized in speech), while this module
returns plain JSON-serializable structures for direct UI rendering. Both
sides read the same underlying data (Postgres tables / Google APIs) so a
task created via the tool-call path shows up here immediately.
"""

from datetime import datetime, timezone
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from zaki.db import get_pool


async def get_tasks(limit: int = 20) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, title, done, due_at FROM tasks "
        "ORDER BY done, due_at NULLS LAST, created_at DESC LIMIT $1",
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "done": r["done"],
            "due_at": r["due_at"].isoformat() if r["due_at"] else None,
        }
        for r in rows
    ]


async def get_notes(limit: int = 6) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, content, created_at FROM notes ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [
        {"id": str(r["id"]), "content": r["content"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]


async def get_calendar_events(
    google_credentials: Credentials | None, max_results: int = 6
) -> list[dict[str, Any]] | None:
    """Returns None (not a list) when Google isn't connected — the frontend
    renders a "connect Google" state rather than an empty calendar.
    """
    if google_credentials is None:
        return None
    service = build("calendar", "v3", credentials=google_credentials)
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=datetime.now(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )
    events = []
    for e in result.get("items", []):
        events.append(
            {
                "id": e.get("id", ""),
                "summary": e.get("summary", "(بدون عنوان)"),
                "start": e["start"].get("dateTime", e["start"].get("date")),
                "link": e.get("htmlLink"),
            }
        )
    return events


async def get_recent_emails(
    google_credentials: Credentials | None, max_results: int = 6
) -> list[dict[str, Any]] | None:
    """Returns None when Google isn't connected, same convention as
    get_calendar_events.
    """
    if google_credentials is None:
        return None
    service = build("gmail", "v1", credentials=google_credentials)
    result = service.users().messages().list(userId="me", maxResults=max_results).execute()
    messages = result.get("messages", [])
    emails = []
    for m in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        emails.append(
            {
                "id": m["id"],
                "from": headers.get("From", "?"),
                "subject": headers.get("Subject", "(بدون عنوان)"),
                "snippet": msg.get("snippet", ""),
            }
        )
    return emails
