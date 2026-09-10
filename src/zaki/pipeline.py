"""Core text -> reply pipeline, extracted from main.py so non-HTTP entry
points (the WhatsApp webhook, the Twilio call audio bridge) can run the
same reasoning loop as the text/audio endpoints without importing main.py
itself (which would be circular, since main.py registers those webhooks).
"""

import logging
import uuid
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

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

logger = logging.getLogger(__name__)

# Fixed, not server-local: this app is deployed on Render, whose containers
# run in UTC regardless of where the user actually is. Using naive
# datetime.now() here worked by accident locally (this machine happens to
# be set to Cairo time) and would have silently been 2-3 hours wrong in
# production — every relative time expression ("الساعة 5", "بكرة الصبح")
# and every reminder resolved from it would land at the wrong time.
_LOCAL_TZ = ZoneInfo("Africa/Cairo")
_ARABIC_WEEKDAYS = [
    "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد",
]


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


@lru_cache
def _get_openai_provider():
    from zaki.config import get_settings
    from zaki.providers.openai_provider import OpenAIProvider

    settings = get_settings()
    return OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_fallback_model,
    )


def _build_cascade(route: Route, settings: Settings) -> list[tuple[LLMProvider, str]]:
    """Provider order for this turn. Claude is never the default first
    call for an ordinary message — only when router.py's own heuristics
    already decided this is an explicit complex-analysis request, or as
    the last resort after both Gemini and the OpenAI cheap-fallback tier
    have failed. The OpenAI tier is included only when OPENAI_API_KEY is
    actually set — otherwise the cascade quietly shrinks to Gemini/Claude,
    same as before this feature existed.
    """
    gemini = (_get_gemini_provider(), settings.gemini_model)
    claude = (_get_claude_provider(), settings.claude_model)
    openai_tier = (
        [(_get_openai_provider(), settings.openai_fallback_model)] if settings.openai_api_key else []
    )

    if route is Route.CLAUDE:
        return [claude, gemini, *openai_tier]
    return [gemini, *openai_tier, claude]


async def _run_cascade(providers: list[tuple[LLMProvider, str]], **kwargs) -> tuple[str, str]:
    last_exc: Exception | None = None
    for provider, model_id in providers:
        try:
            reply = await provider.run_tool_loop(**kwargs)
            return reply, model_id
        except RuntimeError as exc:
            logger.warning("Model tier %s failed, trying next tier if any: %s", model_id, exc)
            last_exc = exc
    assert last_exc is not None  # _build_cascade never returns an empty list
    raise last_exc


def _current_time_context() -> str:
    now = datetime.now(_LOCAL_TZ)
    weekday = _ARABIC_WEEKDAYS[now.weekday()]
    return (
        f"\n\n## الوقت الحالي\nالتاريخ والوقت الآن بتوقيت القاهرة (مصر): "
        f"يوم {weekday}، {now.strftime('%Y-%m-%d')}، الساعة {now.strftime('%H:%M')}."
    )


async def run_assistant_pipeline(
    *,
    text: str,
    session_id: str,
    settings: Settings,
    context: ContextProvider,
) -> AssistantResponse:
    """Shared by /api/assistant, the audio/voice endpoints, the WhatsApp
    webhook, the Twilio call bridge, and the Telegram bot — every entry
    point is just "get text in somehow, run this, get a reply out" against
    the same routing, tools, and conversation history.
    """
    route = choose_route(text, escalation_length=settings.escalation_length)

    bundle = await context.load(session_id)

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
    system_with_time = SYSTEM_PROMPT + _current_time_context()

    cascade = _build_cascade(route, settings)

    # Raises RuntimeError only if every tier in the cascade failed —
    # callers decide how to surface that: HTTP endpoints turn it into a
    # 502, webhook/call/Telegram handlers reply with something spoken/
    # typed instead.
    reply, model_id = await _run_cascade(
        cascade,
        system=system_with_time,
        user_text=text,
        history=bundle.recent_turns,
        tools=tool_registry,
        tool_context=tool_context,
        google_connected=google_credentials is not None,
    )

    await context.record(session_id, text, reply)

    return AssistantResponse(reply=reply, route=route.value, model=model_id)
