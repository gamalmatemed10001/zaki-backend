"""Local system automation — deliberately narrow.

Zaki's tool loop already ingests untrusted content via search_web (a
malicious or compromised web page can put arbitrary text in front of the
model). Pairing that with a general-purpose "run this shell command" tool
would turn a prompt injection into remote code execution on the user's own
machine. So this module offers exactly two capabilities, both incapable of
that:

- open_application: launches one of a fixed, hardcoded set of executables.
  The model supplies a NAME from that set, never a path or command line —
  subprocess.Popen is called with a fixed argv list, never shell=True, so
  there is no string-concatenation/shell-injection surface at all.
- search_local_files: read-only filename search confined to a fixed set of
  well-known user folders (Desktop/Documents/Downloads/Pictures). Every
  candidate path is verified to still resolve inside the chosen root
  (blocks ".." traversal) before being returned. No file contents are ever
  read.

Only works when zaki.main is actually running on the user's own Windows
machine (the local FastAPI/uvicorn process) — meaningless in the Vercel
serverless deployment, which has no access to "the user's desktop" at all.
"""

import os
from pathlib import Path
from typing import Any

from zaki.tools.base import ToolContext, ToolDefinition

# name -> argv for subprocess.Popen(argv). Values are the actual Windows
# executables/commands, resolved via PATH or well-known aliases — never
# user-supplied strings. Add to this list deliberately; never accept an
# arbitrary path or command from a tool argument.
_APP_ALLOWLIST: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "paint": ["mspaint.exe"],
    "browser": ["cmd.exe", "/c", "start", ""],  # "start """ opens the OS default browser
    "task_manager": ["taskmgr.exe"],
    "control_panel": ["control.exe"],
    "settings": ["cmd.exe", "/c", "start", "ms-settings:"],
}

# key -> resolved root directory. Fixed set, no user-supplied paths.
_FOLDER_ALLOWLIST: dict[str, Path] = {
    "desktop": Path.home() / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
    "pictures": Path.home() / "Pictures",
}

_MAX_RESULTS = 20


async def open_application(args: dict[str, Any], context: ToolContext) -> str:
    name = args["app"]
    argv = _APP_ALLOWLIST.get(name)
    if argv is None:
        allowed = ", ".join(sorted(_APP_ALLOWLIST))
        return f"Error: '{name}' isn't in the allowed app list ({allowed})."

    import subprocess

    try:
        subprocess.Popen(argv, shell=False)  # noqa: S603 — fixed argv, never user input
    except FileNotFoundError:
        return f"Error: couldn't launch '{name}' — executable not found on this machine."
    except Exception as exc:
        return f"Error launching '{name}': {exc}"
    return f"Opened {name}."


async def search_local_files(args: dict[str, Any], context: ToolContext) -> str:
    folder_key = args["folder"]
    query = args["query"].lower()

    root = _FOLDER_ALLOWLIST.get(folder_key)
    if root is None:
        allowed = ", ".join(sorted(_FOLDER_ALLOWLIST))
        return f"Error: '{folder_key}' isn't an allowed folder ({allowed})."
    if not root.exists():
        return f"'{folder_key}' folder doesn't exist on this machine."

    resolved_root = root.resolve()
    matches: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(resolved_root):
        for filename in filenames:
            if query not in filename.lower():
                continue
            candidate = (Path(dirpath) / filename).resolve()
            # Defense in depth against symlink/junction traversal escaping
            # the allowed root, even though os.walk started inside it.
            if resolved_root not in candidate.parents and candidate != resolved_root:
                continue
            matches.append(str(candidate))
            if len(matches) >= _MAX_RESULTS:
                break
        if len(matches) >= _MAX_RESULTS:
            break

    if not matches:
        return f"No files matching '{args['query']}' in {folder_key}."
    return "\n".join(f"- {m}" for m in matches)


TOOLS = [
    ToolDefinition(
        name="open_application",
        description=(
            "Open a local application on the user's computer. Only apps in "
            "a fixed allowed list can be opened — if asked for something "
            "outside it, say so plainly rather than trying anything else."
        ),
        parameters={
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "enum": sorted(_APP_ALLOWLIST),
                },
            },
            "required": ["app"],
            "additionalProperties": False,
        },
        handler=open_application,
        requires_google=False,
    ),
    ToolDefinition(
        name="search_local_files",
        description=(
            "Search for files by name within one specific well-known user "
            "folder (Desktop, Documents, Downloads, Pictures). Read-only — "
            "returns matching file paths only, never file contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "enum": sorted(_FOLDER_ALLOWLIST),
                },
                "query": {"type": "string", "description": "substring to match in filenames"},
            },
            "required": ["folder", "query"],
            "additionalProperties": False,
        },
        handler=search_local_files,
        requires_google=False,
    ),
]
