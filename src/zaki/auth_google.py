"""Google OAuth 2.0 — single-user flow (spec §8: tokens never in source).

TokenStore is a Protocol. Step 1/2 used a local-JSON-file stub; step 3
swaps in PostgresTokenStore behind the same interface — no call-site
changes needed beyond making the Protocol's methods async (Postgres access
is inherently async via asyncpg, unlike the file stub's sync disk I/O).
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Protocol

import google.auth.transport.requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from zaki.config import Settings, get_settings
from zaki.db import get_pool

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Gitignored (see .gitignore) — step 1/2 token location. No longer written
# to, but read once by migrate_file_token_to_postgres() at startup so an
# existing connection carries over without redoing the consent screen.
_TOKEN_FILE = Path(".google_token.json")


class TokenStore(Protocol):
    async def save(self, credentials: Credentials) -> None: ...
    async def load(self) -> Credentials | None: ...


class FileTokenStore:
    """Step 1/2 local-JSON-file stub. Kept only as the source for the
    one-time migration into Postgres — no longer used at runtime.
    """

    def __init__(self, path: Path = _TOKEN_FILE) -> None:
        self._path = path

    async def save(self, credentials: Credentials) -> None:
        self._path.write_text(credentials.to_json(), encoding="utf-8")

    async def load(self) -> Credentials | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return Credentials.from_authorized_user_info(data, SCOPES)


class PostgresTokenStore:
    """Real store (build step 3). Single-user system — one fixed row."""

    async def save(self, credentials: Credentials) -> None:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO google_tokens (id, token_json, updated_at) "
            "VALUES ('default', $1, now()) "
            "ON CONFLICT (id) DO UPDATE SET token_json = $1, updated_at = now()",
            credentials.to_json(),
        )

    async def load(self) -> Credentials | None:
        pool = await get_pool()
        row = await pool.fetchrow("SELECT token_json FROM google_tokens WHERE id = 'default'")
        if row is None:
            return None
        data = json.loads(row["token_json"])
        return Credentials.from_authorized_user_info(data, SCOPES)


_token_store = PostgresTokenStore()


def get_token_store() -> TokenStore:
    return _token_store


async def migrate_file_token_to_postgres() -> None:
    """One-time carry-over so completing Step 2's OAuth flow isn't wasted.
    Called from main.py's lifespan; safe to call every startup — no-ops
    once Postgres already has a row or the old file doesn't exist.
    """
    file_store = FileTokenStore()
    existing = await _token_store.load()
    if existing is not None:
        return
    creds = await file_store.load()
    if creds is None:
        return
    await _token_store.save(creds)


def _require_oauth_config(settings: Settings) -> tuple[str, str]:
    if settings.google_oauth_client_id is None or settings.google_oauth_client_secret is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Google OAuth isn't configured — set GOOGLE_OAUTH_CLIENT_ID and "
                "GOOGLE_OAUTH_CLIENT_SECRET in .env first."
            ),
        )
    return (
        settings.google_oauth_client_id.get_secret_value(),
        settings.google_oauth_client_secret.get_secret_value(),
    )


def _build_flow(settings: Settings) -> Flow:
    client_id, client_secret = _require_oauth_config(settings)
    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


async def get_google_credentials(store: TokenStore) -> Credentials | None:
    """Loaded, auto-refreshed credentials, or None if not yet authorized.

    creds.refresh() is a blocking call (google-auth uses `requests`
    internally, not an async HTTP client) — acceptable here: refreshes are
    infrequent (once per ~1hr access-token lifetime) and this is a
    low-traffic single-user app, not a high-concurrency service.
    """
    creds = await store.load()
    if creds is None:
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
        await store.save(creds)
    return creds


router = APIRouter(prefix="/auth/google", tags=["google-oauth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

# google-auth-oauthlib auto-enables PKCE: authorization_url() generates a
# code_verifier bound to that specific Flow *object*. The token exchange in
# callback() must reuse the SAME object, not a freshly built Flow — a new
# one has no verifier and Google rejects the exchange with
# "invalid_grant: Missing code verifier." Keyed by the OAuth `state` param
# (which also gives CSRF protection almost for free). In-memory and
# single-process only — fine for this single-user local deployment; a
# multi-instance/serverless deployment would need this in a shared store.
_pending_flows: dict[str, Flow] = {}


@router.get("/login")
def login(settings: SettingsDep) -> RedirectResponse:
    flow = _build_flow(settings)
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    _pending_flows[state] = flow
    return RedirectResponse(auth_url)


@router.get("/callback")
async def callback(code: str, state: str) -> dict[str, str]:
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise HTTPException(
            status_code=400,
            detail="OAuth state not found or already used — start over at /auth/google/login",
        )
    flow.fetch_token(code=code)
    await _token_store.save(flow.credentials)
    return {"status": "connected", "message": "تم الربط بحساب جوجل بنجاح"}


@router.get("/status")
async def status() -> dict[str, bool]:
    try:
        creds = await get_google_credentials(_token_store)
    except Exception:
        # Same RefreshError-on-a-dead-token failure mode fixed in
        # pipeline.py and /api/dashboard — an expired token with a
        # revoked/invalid refresh_token must report "not connected" here
        # too, not crash this status check with a 500.
        logger.exception("Google credentials load/refresh failed in /auth/google/status")
        return {"connected": False}
    return {"connected": creds is not None and creds.valid}
