"""Core text -> reply pipeline, extracted from main.py so non-HTTP entry
points (the WhatsApp webhook, the Twilio call audio bridge) can run the
same reasoning loop as the text/audio endpoints without importing main.py
itself (which would be circular, since main.py registers those webhooks).
"""

import uuid
from datetime import datetime
from functools import lru_cache

from zaki.auth_google import get_google_credentials, get_token_store
from zaki.config import Settings
from zaki.context import ContextProvider
from zaki.prompts import SYSTEM_PROMPT
from zaki.providers.base import LLMProvider
from zaki.providers.claude import ClaudeProvider
from zaki.providers.gemini import GeminiProvider
from zaki.router import Route, choose_route
from zaki.schemas import AssistantResponse
from zaki.tools.base import ToolContext


@lru_cache
def _get_gemini_provider() -> GeminiProvider:
    from zaki.config import get_settings

    settings = get_settings()
    return GeminiProvider(
        api_key=settings.google_api_key.get_secret_value(),
        model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
    )


@lru_cache
def _get_claude_provider() -> ClaudeProvider:
    from zaki.config import get_settings

    settings = get_settings()
    return ClaudeProvider(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.claude_model,
        effort=settings.claude_effort,
    )


async def run_assistant_pipeline(
    *,
    text: str,
    session_id: str,
    settings: Settings,
    context: ContextProvider,
) -> AssistantResponse:
    """Shared by /api/assistant, the audio/voice endpoints, the WhatsApp
    webhook, and the Twilio call bridge — every entry point is just "get
    text in somehow, run this, get a reply out" against the same routing,
    tools, and conversation history.
    """
    route = choose_route(text, escalation_length=settings.escalation_length)

    bundle = await context.load(session_id)

    provider: LLMProvider
    model_id: str
    if route is Route.CLAUDE:
        provider = _get_claude_provider()
        model_id = settings.claude_model
    else:
        provider = _get_gemini_provider()
        model_id = settings.gemini_model

    # Google credentials are loaded fresh per request — cheap (an indexed
    # single-row Postgres lookup + refresh-if-expired) and keeps the
    # "connected?" state accurate without a background refresh job.
    google_credentials = await get_google_credentials(get_token_store())
    tool_context = ToolContext(
        session_id=session_id,
        turn_id=uuid.uuid4().hex,
        google_credentials=google_credentials,
    )

    # Deferred import: zaki.tools' package __init__ builds its registry
    # from every tools/*.py module, including telephony.py, which imports
    # this module (pipeline.py) for the same reason main.py does — a
    # top-level import here would be circular. By call time (well after
    # app startup) zaki.tools is always fully initialized.
    from zaki.tools import registry as tool_registry

    # create_reminder needs the model to resolve relative time expressions
    # ("الساعة 5 مساءً", "خلال ساعة") into an absolute ISO datetime itself —
    # it has no other way to know "now" otherwise. Appended per-request
    # (not baked into the static SYSTEM_PROMPT) so it's always current.
    system_with_time = (
        f"{SYSTEM_PROMPT}\n\n## الوقت الحالي\nالتاريخ والوقت الآن: "
        f"{datetime.now().isoformat(timespec='seconds')}."
    )

    # Raises RuntimeError on failure (e.g. provider API error) — callers
    # decide how to surface that: HTTP endpoints turn it into a 502,
    # webhook/call handlers reply with something spoken/typed instead.
    reply = await provider.run_tool_loop(
        system=system_with_time,
        user_text=text,
        history=bundle.recent_turns,
        tools=tool_registry,
        tool_context=tool_context,
        google_connected=google_credentials is not None,
    )

    await context.record(session_id, text, reply)

    return AssistantResponse(reply=reply, route=route.value, model=model_id)
