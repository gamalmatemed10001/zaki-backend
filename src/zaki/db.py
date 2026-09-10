"""Postgres connection pool + schema migrations (spec §3.4, build step 3).

DATABASE_URL (Supabase's pgbouncer transaction-mode pooler) backs the app's
runtime connection pool. DIRECT_URL (session-mode / direct connection) is
used only for migrations — pgbouncer transaction mode doesn't reliably
support prepared statements or multi-statement DDL batches.
"""

import asyncpg

from zaki.config import get_settings

_pool: asyncpg.Pool | None = None

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    done BOOLEAN NOT NULL DEFAULT FALSE,
    due_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversation_log_session
    ON conversation_log (session_id, created_at DESC);

-- Not in the spec's §3.4 draft table list, but needed to migrate TokenStore
-- off the local JSON file per build step 3. Single-user system: one row.
CREATE TABLE IF NOT EXISTS google_tokens (
    id TEXT PRIMARY KEY DEFAULT 'default',
    token_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        # statement_cache_size=0 is required through Supabase's pgbouncer
        # transaction-mode pooler — it doesn't support asyncpg's prepared
        # statement caching across pooled connections (each query may land
        # on a different backend connection between calls).
        _pool = await asyncpg.create_pool(
            settings.database_url.get_secret_value(),
            statement_cache_size=0,
            min_size=1,
            max_size=5,
        )
    return _pool


async def run_migrations() -> None:
    settings = get_settings()
    conn = await asyncpg.connect(settings.direct_url.get_secret_value())
    try:
        await conn.execute(_SCHEMA)
    finally:
        await conn.close()


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
