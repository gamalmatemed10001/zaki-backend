"""Model routing heuristic — spec §3.3.0: "simple heuristic ... a more
principled classifier can replace this later if misrouting becomes a
problem." Deliberately pure (no I/O, no API calls) so it's unit-testable
without any credentials and trivially swappable later.
"""

from enum import Enum

# Egyptian Arabic cues that signal a request wants real analysis/reasoning
# rather than a quick lookup or confirmation.
_COMPLEXITY_CUES = (
    "حلل",  # analyze
    "فكر معايا",  # think it through with me
    "قارن",  # compare
    "اشرح بالتفصيل",  # explain in detail
    "رأيك ايه",  # what's your opinion
    "رأيك إيه",
    "ايه الأفضل",  # what's best
    "إيه الأفضل",
)


class Route(str, Enum):
    GEMINI = "gemini"
    CLAUDE = "claude"


def choose_route(text: str, *, escalation_length: int = 280) -> Route:
    """Gemini Flash by default (fast/cheap); escalate to Claude on explicit
    complexity cues or long open-ended input, per spec routing logic.
    """
    if len(text) > escalation_length:
        return Route.CLAUDE
    if any(cue in text for cue in _COMPLEXITY_CUES):
        return Route.CLAUDE
    return Route.GEMINI
