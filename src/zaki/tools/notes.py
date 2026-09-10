"""Quick notes tool — spec §3.3.1: "Custom DB table." Postgres-backed, per
build step 3. Retrieval is recency/keyword-based per spec §3.4 ("V1 ...
Vector/semantic search is a V2 upgrade").
"""

from typing import Any

from zaki.db import get_pool
from zaki.tools.base import ToolContext, ToolDefinition


async def create_note(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO notes (content) VALUES ($1) RETURNING id", args["content"]
    )
    return f"Note saved (id: {row['id']})"


async def list_notes(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    limit = args.get("limit", 10)
    rows = await pool.fetch(
        "SELECT id, content, created_at FROM notes ORDER BY created_at DESC LIMIT $1", limit
    )
    if not rows:
        return "No notes yet."
    return "\n".join(f"- [{r['id']}] {r['content']} ({r['created_at']})" for r in rows)


async def search_notes(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, content, created_at FROM notes WHERE content ILIKE $1 "
        "ORDER BY created_at DESC LIMIT 10",
        f"%{args['query']}%",
    )
    if not rows:
        return f"No notes matching '{args['query']}'."
    return "\n".join(f"- [{r['id']}] {r['content']} ({r['created_at']})" for r in rows)


TOOLS = [
    ToolDefinition(
        name="create_note",
        description="Save a quick note.",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
        handler=create_note,
        requires_google=False,
    ),
    ToolDefinition(
        name="list_notes",
        description="List the most recent notes.",
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "default 10"}},
            "required": [],
            "additionalProperties": False,
        },
        handler=list_notes,
        requires_google=False,
    ),
    ToolDefinition(
        name="search_notes",
        description="Keyword search over saved notes (simple substring match — semantic search is a later upgrade).",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_notes,
        requires_google=False,
    ),
]
