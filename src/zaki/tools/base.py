"""Tool definition shared across both vendors — a single JSON Schema per
tool, rendered to Claude's and Gemini's own tool-schema shapes by
registry.py, so a tool is written once and callable from either model.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from google.oauth2.credentials import Credentials


@dataclass
class ToolContext:
    """Per-request context handlers need. Never part of the tool's JSON
    Schema, so the model can never supply or override its own session,
    turn, or credentials via a tool-call argument.
    """

    session_id: str
    turn_id: str
    google_credentials: Credentials | None


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema — vendor-agnostic
    handler: ToolHandler
    requires_google: bool = True
