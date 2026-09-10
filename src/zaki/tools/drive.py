"""Drive tool — read-only for V1 (spec §3.3.1: write access deferred to V2)."""

import io
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from zaki.tools.base import ToolContext, ToolDefinition

_MAX_CHARS = 4000  # keep tool results small — this feeds straight back into the model's context


def _service(context: ToolContext):
    if context.google_credentials is None:
        raise RuntimeError("Google account not connected yet")
    return build("drive", "v3", credentials=context.google_credentials)


async def list_files(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    query = args.get("query")
    result = (
        service.files()
        .list(q=query, pageSize=15, fields="files(id, name, mimeType)")
        .execute()
    )
    files = result.get("files", [])
    if not files:
        return "No files found."
    return "\n".join(f"- [{f['id']}] {f['name']} ({f['mimeType']})" for f in files)


async def read_file(args: dict[str, Any], context: ToolContext) -> str:
    service = _service(context)
    file_id = args["file_id"]
    meta = service.files().get(fileId=file_id, fields="name, mimeType").execute()
    mime = meta["mimeType"]

    if mime == "application/vnd.google-apps.document":
        content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        return f"{meta['name']}:\n\n{text[:_MAX_CHARS]}"

    if mime.startswith("text/"):
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        text = buf.getvalue().decode("utf-8", errors="replace")
        return f"{meta['name']}:\n\n{text[:_MAX_CHARS]}"

    return (
        f"'{meta['name']}' is a {mime} file — binary formats aren't readable "
        "yet, only Google Docs and plain text."
    )


TOOLS = [
    ToolDefinition(
        name="list_drive_files",
        description=(
            "Search/list files in Google Drive. Optional query uses Drive "
            "search syntax (e.g. \"name contains 'report'\")."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        handler=list_files,
    ),
    ToolDefinition(
        name="read_drive_file",
        description="Read the text content of a Google Doc or plain-text file by id (from list_drive_files).",
        parameters={
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
            "additionalProperties": False,
        },
        handler=read_file,
    ),
]
