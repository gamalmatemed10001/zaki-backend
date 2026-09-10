"""Common provider interface so main.py never branches on vendor beyond
route selection — both Gemini and Claude providers implement this.

Each provider owns its own tool-calling loop (run_tool_loop) rather than
sharing a vendor-agnostic loop: Claude and Gemini have genuinely different
message/content shapes, and forcing them through one intermediate
representation would cost more than it saves. This mirrors generate()'s
existing design, where each provider already owns its full request shape.
"""

from typing import Protocol

from zaki.context import Turn
from zaki.tools.base import ToolContext
from zaki.tools.registry import ToolRegistry


class LLMProvider(Protocol):
    async def generate(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
    ) -> str: ...

    async def run_tool_loop(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
        tools: ToolRegistry,
        tool_context: ToolContext,
        google_connected: bool,
    ) -> str: ...
