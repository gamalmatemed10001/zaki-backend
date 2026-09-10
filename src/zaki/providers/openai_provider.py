"""OpenAI cheap-fallback provider — gpt-4o-mini, current as of 2026-09.

Sits between Gemini and Claude in pipeline.py's cascade: cheaper and
faster than Claude, used only when Gemini (including its own internal
gemini_fallback_model retry) has already failed. Never the first choice
for a normal turn — this exists purely to avoid a total outage (or an
unnecessary Claude spend) when Gemini has a bad day.
"""

import json

import openai

from zaki.context import Turn
from zaki.providers.base import LLMProvider
from zaki.tools.base import ToolContext
from zaki.tools.registry import ToolRegistry

_MAX_TOOL_ITERATIONS = 5


class OpenAIProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._model = model

    def _initial_messages(self, system: str, user_text: str, history: list[Turn]) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system}]
        for turn in history:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})
        messages.append({"role": "user", "content": user_text})
        return messages

    async def _create(self, *, messages: list[dict], tools: list[dict] | None):
        try:
            kwargs = dict(model=self._model, messages=messages)
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            return await self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise RuntimeError("OpenAI rate limited — try again shortly") from exc
        except openai.APIStatusError as exc:
            raise RuntimeError(f"OpenAI API error ({exc.status_code}): {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise RuntimeError("Could not reach OpenAI — check network") from exc

    async def generate(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
    ) -> str:
        messages = self._initial_messages(system, user_text, history)
        response = await self._create(messages=messages, tools=None)
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned no text content")
        return content

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
        messages = self._initial_messages(system, user_text, history)
        tool_defs = tools.render_for_openai(google_connected=google_connected)

        for _ in range(_MAX_TOOL_ITERATIONS):
            response = await self._create(messages=messages, tools=tool_defs or None)
            message = response.choices[0].message

            if not message.tool_calls:
                if not message.content:
                    raise RuntimeError("OpenAI returned no text content")
                return message.content

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in message.tool_calls
                    ],
                }
            )

            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result_text = await tools.execute(tc.function.name, args, tool_context)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        raise RuntimeError("OpenAI tool loop exceeded max iterations")
