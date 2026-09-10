"""Aggregates ToolDefinitions and renders them to each vendor's schema
format from the single JSON-Schema source of truth in each ToolDefinition.
"""

from typing import Any

from google.genai import types as genai_types

from zaki.tools.base import ToolContext, ToolDefinition

# Gemini's function-calling `parameters` field is a restricted OpenAPI 3.0
# subset, not full JSON Schema — it rejects unrecognized keywords like
# `additionalProperties` outright (400 INVALID_ARGUMENT). Every ToolDefinition
# here sets that key for Claude's `strict`-style validation; strip it (and
# recurse into nested `properties`/`items`) before handing schemas to Gemini.
_UNSUPPORTED_GEMINI_KEYS = {"additionalProperties"}


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in schema.items() if k not in _UNSUPPORTED_GEMINI_KEYS}
    if "properties" in cleaned:
        cleaned["properties"] = {
            key: _to_gemini_schema(value) if isinstance(value, dict) else value
            for key, value in cleaned["properties"].items()
        }
    if "items" in cleaned and isinstance(cleaned["items"], dict):
        cleaned["items"] = _to_gemini_schema(cleaned["items"])
    return cleaned


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {t.name: t for t in tools}

    def available(self, *, google_connected: bool) -> list[ToolDefinition]:
        if google_connected:
            return list(self._tools.values())
        return [t for t in self._tools.values() if not t.requires_google]

    def render_for_claude(self, *, google_connected: bool) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in self.available(google_connected=google_connected)
        ]

    def render_for_openai(self, *, google_connected: bool) -> list[dict[str, Any]]:
        # OpenAI's function schema accepts additionalProperties directly
        # (no stripping needed like Gemini) — same JSON Schema shape as
        # Claude's input_schema, just wrapped differently.
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.available(google_connected=google_connected)
        ]

    def render_for_gemini(self, *, google_connected: bool) -> list[genai_types.Tool]:
        declarations = [
            genai_types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=_to_gemini_schema(t.parameters),
            )
            for t in self.available(google_connected=google_connected)
        ]
        if not declarations:
            return []
        return [genai_types.Tool(function_declarations=declarations)]

    async def execute(self, name: str, args: dict[str, Any], context: ToolContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return await tool.handler(args, context)
        except Exception as exc:  # a broken tool must not crash the whole loop
            return f"Error running {name}: {exc}"
