"""Tasks/reminders tool — spec §3.3.1: "Custom DB table; sync with Apple
Reminders/Google Tasks deferred to V2." Postgres-backed, per build step 3.
"""

from typing import Any

from zaki.db import get_pool
from zaki.tools.base import ToolContext, ToolDefinition


async def create_task(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO tasks (title, due_at) VALUES ($1, $2) RETURNING id",
        args["title"],
        args.get("due_at"),
    )
    return f"Task created: {args['title']} (id: {row['id']})"


async def list_tasks(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    include_done = args.get("include_done", False)
    if include_done:
        rows = await pool.fetch(
            "SELECT id, title, done, due_at FROM tasks ORDER BY done, due_at NULLS LAST, created_at"
        )
    else:
        rows = await pool.fetch(
            "SELECT id, title, done, due_at FROM tasks WHERE done = FALSE "
            "ORDER BY due_at NULLS LAST, created_at"
        )
    if not rows:
        return "No tasks." if not include_done else "No tasks at all, done or not."
    lines = []
    for r in rows:
        mark = "✓" if r["done"] else "○"
        due = f" (due {r['due_at']})" if r["due_at"] else ""
        lines.append(f"- [{mark}] [{r['id']}] {r['title']}{due}")
    return "\n".join(lines)


async def complete_task(args: dict[str, Any], context: ToolContext) -> str:
    pool = await get_pool()
    # $1::uuid: the model passes the id as a plain string (JSON has no UUID
    # type); the explicit cast lets Postgres validate/convert it rather than
    # relying on asyncpg's Python-side UUID codec accepting a bare str.
    try:
        row = await pool.fetchrow(
            "UPDATE tasks SET done = TRUE, completed_at = now() WHERE id = $1::uuid RETURNING title",
            args["task_id"],
        )
    except Exception:
        return f"Error: '{args['task_id']}' isn't a valid task id"
    if row is None:
        return f"Error: no task with id {args['task_id']}"
    return f"Marked done: {row['title']}"


TOOLS = [
    ToolDefinition(
        name="create_task",
        description="Create a new task/reminder.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due_at": {"type": "string", "description": "ISO 8601 datetime, optional"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        handler=create_task,
        requires_google=False,
    ),
    ToolDefinition(
        name="list_tasks",
        description="List tasks. By default only pending (not-done) tasks.",
        parameters={
            "type": "object",
            "properties": {
                "include_done": {"type": "boolean", "description": "default false"},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=list_tasks,
        requires_google=False,
    ),
    ToolDefinition(
        name="complete_task",
        description="Mark a task done by its id (from list_tasks).",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "UUID from list_tasks"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
        handler=complete_task,
        requires_google=False,
    ),
]
