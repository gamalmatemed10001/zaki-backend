"""Gemini default/fast provider — gemini-3.5-flash, current as of 2026-09.

Uses `google-genai` (the unified SDK, `from google import genai`), NOT the
deprecated `google.generativeai` package.

NOTE: both the async `client.aio.models.generate_content(...)` signature
and the function-calling shapes (`types.Tool`, `types.FunctionDeclaration`,
`types.FunctionResponse`, reading `function_call` off response parts) are
the least-verified part of this codebase — google-genai is newer than the
deprecated SDK it replaces and wasn't exercised end-to-end at write time
here (Step 2's Google tools need live OAuth to test). If any of these
raise a TypeError/AttributeError against the installed version, trust the
traceback over this comment and adjust the call shape accordingly.
"""

import logging

from google import genai
from google.genai import types

from zaki.context import Turn
from zaki.providers.base import LLMProvider
from zaki.tools.base import ToolContext
from zaki.tools.registry import ToolRegistry

_MAX_TOOL_ITERATIONS = 5

logger = logging.getLogger(__name__)

# google-genai's exception hierarchy for these two cases wasn't confirmed
# against the installed version at write time (same "least-verified" flag
# as the module docstring above) — matching on the status text is what we
# actually observed live in this project's own error output (both a real
# 429 RESOURCE_EXHAUSTED response and Google's documented 503 UNAVAILABLE
# wording), and degrades safely: worst case a fallback-worthy error slips
# through as a hard failure rather than silently mis-triggering on
# something unrelated.
_FALLBACK_WORTHY_MARKERS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")


def _is_fallback_worthy(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _FALLBACK_WORTHY_MARKERS)


class GeminiProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str, fallback_model: str | None = None) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # None/same-as-primary both disable fallback — nothing meaningful
        # to retry against otherwise.
        self._fallback_model = fallback_model if fallback_model != model else None

    def _initial_contents(self, user_text: str, history: list[Turn]) -> list[types.Content]:
        contents: list[types.Content] = []
        for turn in history:
            contents.append(types.Content(role="user", parts=[types.Part(text=turn.user)]))
            contents.append(types.Content(role="model", parts=[types.Part(text=turn.assistant)]))
        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        return contents

    async def _generate_with_fallback(
        self, *, contents: list[types.Content], config: types.GenerateContentConfig
    ):
        """Tries the primary model; on a quota/overload error, logs it and
        retries once against the fallback model before giving up. Returns
        the raw SDK response — callers pull .text or .candidates off it.
        """
        try:
            return await self._client.aio.models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except Exception as primary_exc:
            if not self._fallback_model or not _is_fallback_worthy(primary_exc):
                raise RuntimeError(f"Gemini request failed: {primary_exc}") from primary_exc

            logger.warning(
                "Gemini model %s hit a quota/availability error, falling back to %s: %s",
                self._model,
                self._fallback_model,
                primary_exc,
            )
            try:
                return await self._client.aio.models.generate_content(
                    model=self._fallback_model, contents=contents, config=config
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"Gemini request failed on both {self._model} and fallback "
                    f"{self._fallback_model}: {fallback_exc}"
                ) from fallback_exc

    async def generate(
        self,
        *,
        system: str,
        user_text: str,
        history: list[Turn],
    ) -> str:
        contents = self._initial_contents(user_text, history)
        response = await self._generate_with_fallback(
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system),
        )

        if not response.text:
            raise RuntimeError("Gemini returned no text content")
        return response.text

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
        contents = self._initial_contents(user_text, history)
        gemini_tools = tools.render_for_gemini(google_connected=google_connected)
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=gemini_tools or None,
        )

        for _ in range(_MAX_TOOL_ITERATIONS):
            response = await self._generate_with_fallback(contents=contents, config=config)

            candidate = response.candidates[0]
            function_calls = [
                part.function_call for part in candidate.content.parts if part.function_call
            ]

            if not function_calls:
                if not response.text:
                    raise RuntimeError("Gemini returned no text content")
                return response.text

            contents.append(candidate.content)

            response_parts = []
            for fc in function_calls:
                result_text = await tools.execute(fc.name, dict(fc.args or {}), tool_context)
                response_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name, response={"result": result_text}
                        )
                    )
                )
            contents.append(types.Content(role="user", parts=response_parts))

        raise RuntimeError("Gemini tool loop exceeded max iterations")
