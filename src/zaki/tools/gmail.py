"""Gmail tool — read, draft, and send.

Full-send capability is enabled per user decision (2026-09-07), but a
prompt-only "ask before sending" instruction is weak on its own: a model
can chain draft_email -> send_pending_email inside a single agentic turn
without any real human ever seeing the draft. So the gate here is
structural, not just prompted:

send_pending_email only succeeds if the pending draft was staged on a
PREVIOUS /api/assistant call (a different turn_id) — i.e. an actual HTTP
round-trip happened, meaning the user genuinely saw Zaki's read-back and
replied, before the send tool can fire. The system prompt (prompts.py)
reinforces this as a backstop, but the enforcement that actually matters
lives here.
"""

import base64
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Any

from googleapiclient.discovery import build

from zaki.tools.base import ToolContext, ToolDefinition


def _service(context: ToolContext):
    if context.google_credentials is None:
        raise RuntimeError("Google account not connected yet")
    return build("gmail", "v1", credentials=context.google_credentials)


@dataclass
class PendingDraft:
    to: str
    subject: str
    body: str
    created_turn_id: str


# Module-level, per-session — mirrors the InMemoryContextProvider pattern in
# context.py. A real store (Postgres) takes over at build step 3.
_pending_drafts: dict[str, PendingDraft] = {}


async def list_recent(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    max_results = args.get("max_results", 10)
    result = service.users().messages().list(userId="me", maxResults=max_results).execute()
    messages = result.get("messages", [])
    if not messages:
        return "No recent messages."
    lines = []
    for m in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        lines.append(f"- [{m['id']}] From: {headers.get('From', '?')} — {headers.get('Subject', '(no subject)')}")
    return "\n".join(lines)


async def read_email(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    msg = service.users().messages().get(userId="me", id=args["message_id"], format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    snippet = msg.get("snippet", "")
    return f"From: {headers.get('From', '?')}\nSubject: {headers.get('Subject', '?')}\n\n{snippet}"


async def draft_email(args: dict[str, Any], context: ToolContext) -> str:
    # Staged locally — no Gmail API call needed until send_pending_email.
    _pending_drafts[context.session_id] = PendingDraft(
        to=args["to"],
        subject=args["subject"],
        body=args["body"],
        created_turn_id=context.turn_id,
    )
    return (
        f"Draft staged (NOT sent). To: {args['to']} | Subject: {args['subject']}\n"
        f"Body: {args['body']}\n"
        "Read this back to the user in full and wait for their explicit "
        "confirmation on their NEXT message before ever calling "
        "send_pending_email — do not call it in this same turn; it will be "
        "rejected if you do."
    )


async def send_pending_email(args: dict[str, Any], context: ToolContext) -> str:
    draft = _pending_drafts.get(context.session_id)
    if draft is None:
        return "Error: no pending draft for this session. Draft one first with draft_email."

    if draft.created_turn_id == context.turn_id:
        # The structural gate: this draft was staged THIS turn, so no human
        # round-trip has happened yet. Refuse regardless of what the model
        # or its instructions say.
        return (
            "Error: this draft was staged earlier in the current turn — no "
            "human has confirmed it yet. Read the draft back to the user and "
            "STOP. Only call send_pending_email again after their next "
            "message explicitly confirms."
        )

    service = _service(context)
    mime = MIMEText(draft.body)
    # Capitalized keys matter: Gmail delivers regardless of header casing,
    # but list_recent/read_email above read messages back via
    # metadataHeaders=["From", "Subject"], which matches case-sensitively.
    # Lowercase "to"/"subject" here would make Zaki's own sent mail show up
    # as "From: ? — (no subject)" when it later reads it back.
    mime["To"] = draft.to
    mime["Subject"] = draft.subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    del _pending_drafts[context.session_id]
    return f"Sent. Message ID: {sent.get('id')}"


TOOLS = [
    ToolDefinition(
        name="list_recent_emails",
        description="List recent Gmail messages (id, sender, subject).",
        parameters={
            "type": "object",
            "properties": {"max_results": {"type": "integer", "description": "default 10"}},
            "required": [],
            "additionalProperties": False,
        },
        handler=list_recent,
    ),
    ToolDefinition(
        name="read_email",
        description="Read the full content of one Gmail message by id (from list_recent_emails).",
        parameters={
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
        handler=read_email,
    ),
    ToolDefinition(
        name="draft_email",
        description=(
            "Stage an email draft. Does NOT send. Always read the draft back "
            "to the user in Masri and wait for their next message to "
            "explicitly confirm before ever calling send_pending_email."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        handler=draft_email,
    ),
    ToolDefinition(
        name="send_pending_email",
        description=(
            "Send the most recently staged draft for this session. Only "
            "call this after the user has explicitly confirmed, in their "
            "own separate message, that they want it sent. Calling it in "
            "the same turn as draft_email will be rejected."
        ),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=send_pending_email,
    ),
]
