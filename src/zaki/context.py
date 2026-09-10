"""Memory/context layer contract (spec §3.4).

Step 1 defined the interface + an in-memory stub. Step 3 adds the real
Postgres-backed implementation (conversation_log table) behind the same
Protocol — main.py needed zero changes to pick it up.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Protocol

from zaki.db import get_pool


@dataclass(frozen=True)
class Turn:
    user: str
    assistant: str


@dataclass(frozen=True)
class ContextBundle:
    """What gets injected into the system/user prompt for a turn."""

    recent_turns: list[Turn] = field(default_factory=list)


class ContextProvider(Protocol):
    async def load(self, session_id: str) -> ContextBundle: ...

    async def record(self, session_id: str, user: str, assistant: str) -> None: ...


class InMemoryContextProvider:
    """Process-local stub. Lost on restart — fine for step 1; Postgres
    swaps in later behind the same Protocol.
    """

    def __init__(self, *, max_turns_per_session: int = 10) -> None:
        self._max_turns = max_turns_per_session
        self._sessions: dict[str, deque[Turn]] = defaultdict(
            lambda: deque(maxlen=max_turns_per_session)
        )

    async def load(self, session_id: str) -> ContextBundle:
        return ContextBundle(recent_turns=list(self._sessions[session_id]))

    async def record(self, session_id: str, user: str, assistant: str) -> None:
        self._sessions[session_id].append(Turn(user=user, assistant=assistant))


class PostgresContextProvider:
    """Real store (spec §3.4): conversation_log, retrieved by recency —
    the same interface InMemoryContextProvider used, so main.py needed no
    changes to switch over.
    """

    def __init__(self, *, max_turns: int = 10) -> None:
        self._max_turns = max_turns

    async def load(self, session_id: str) -> ContextBundle:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT user_text, assistant_text FROM conversation_log "
            "WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2",
            session_id,
            self._max_turns,
        )
        # rows come back newest-first; reverse so history reads oldest-to-newest
        turns = [Turn(user=r["user_text"], assistant=r["assistant_text"]) for r in reversed(rows)]
        return ContextBundle(recent_turns=turns)

    async def record(self, session_id: str, user: str, assistant: str) -> None:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO conversation_log (session_id, user_text, assistant_text) "
            "VALUES ($1, $2, $3)",
            session_id,
            user,
            assistant,
        )


_default_provider = PostgresContextProvider()


def get_context_provider() -> ContextProvider:
    return _default_provider
