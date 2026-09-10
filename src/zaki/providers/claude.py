"""Claude escalation provider — claude-sonnet-5, current as of 2026-09.
Switched from claude-opus-5 to Sonnet on 2026-09-07 per user cost/latency
decision (config.py has the full note).

Key API facts this file relies on (see claude-api skill — these hold for
both Opus 5 and Sonnet 5):
- `budget_tokens` is removed on Sonnet 5 and returns 400; thinking is
  adaptive-only, depth is tuned via output_config.effort instead.
- Assistant message prefill is removed on Sonnet 5 — not used here anyway.
- response.content is a list of typed blocks; must check .type == "text".
- stop_reason == "refusal" must be checked before reading content.
- Parallel tool use: return ALL tool_result blocks in a single user
  message, never split across messages — splitting trains the model away
  from parallel calls.
"""

import anthropic

from zaki.context import Turn
from zaki.providers.base import LLMProvider
from zaki.tools.base import ToolContext
from zaki.tools.registry import ToolRegistry

_MAX_TOOL_ITERATIONS = 5


class ClaudeProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str, effort: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._effort = effort

    def _initial_messages(self, user_text: str, history: list[Turn]) -> list[dict]:
        messages: list[dict] = []
        for turn in history:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})
        messages.append({"role": "user", "content": user_text})
        return messages

    async def _create(self, *, system: str, messages: list[dict], tools: list[dict] | None):
        try:
            kwargs = dict(
                model=self._model,
                max_tokens=8000,
                system=system,
                messages=messages,
                output_config={"effort": self._effort},
            )
            if tools:
                kwargs["tools"] = tools
            return await self._client.messages.create(**kwargs)
        except anthropic.NotFoundError as exc:
            raise RuntimeError(f"Claude model '{self._model}' not found") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError("Claude rate limited — try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"Claude API error ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError("Could not reach Claude — check network") from exc

    def _check_refusal(self, response) -> None:
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise RuntimeError(f"Claude declined to respond (category: {category})")

    async def generate(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
    ) -> str:
        messages = self._initial_messages(user_text, history)
        response = await self._create(system=system, messages=messages, tools=None)
        self._check_refusal(response)
        for block in response.content:
            if block.type == "text":
                return block.text
        raise RuntimeError("Claude returned no text content")

    async def run_tool_loop(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
        tools: ToolRegistry,
        tool_context: ToolContext,
        google_connected: bool,
    ) -> str:
        messages = self._initial_messages(user_text, history)
        tool_defs = tools.render_for_claude(google_connected=google_connected)

        for _ in range(_MAX_TOOL_ITERATIONS):
            response = await self._create(system=system, messages=messages, tools=tool_defs or None)
            self._check_refusal(response)

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                for block in response.content:
                    if block.type == "text":
                        return block.text
                raise RuntimeError("Claude returned no text content")

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_use_blocks:
                result_text = await tools.execute(block.name, block.input, tool_context)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
                )
            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError("Claude tool loop exceeded max iterations")
