"""Central settings. Model IDs live here, never inline in provider code —
that's the direct answer to the spec's "confirm the current model ID at
build time" warnings: a future vendor rename is an env var change, not a
code edit.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets ----------------------------------------------------------
    # Google/Anthropic keys read the vendor SDKs' own conventional env var
    # names (no ZAKI_ prefix); the endpoint token is ours, so it gets one.
    zaki_api_key: SecretStr = Field(validation_alias="ZAKI_API_KEY")
    google_api_key: SecretStr = Field(validation_alias="GOOGLE_API_KEY")
    anthropic_api_key: SecretStr = Field(validation_alias="ANTHROPIC_API_KEY")

    # --- Model routing ------------------------------------------------------
    # Verified at build time 2026-09: gemini-1.5-flash and gemini-1.5-pro
    # are both fully shut down (404) — a later request (2026-09) to set
    # gemini-1.5-flash as the default was declined for exactly this reason
    # and gemini-3.5-flash kept instead. Current Flash line is 3.8 / 3.5 /
    # 3.5-lite, none deprecated. 3.5-flash chosen over 3.5-flash-lite:
    # reply quality matters more than the lite tier's marginal cost savings
    # for this workload.
    gemini_model: str = Field("gemini-3.5-flash", validation_alias="ZAKI_GEMINI_MODEL")

    # Fallback model GeminiProvider retries against on 429 (quota exhausted)
    # or 503 (overloaded) — a distinct, currently-live Flash-tier model
    # rather than a second copy of the primary, so a per-model quota limit
    # on gemini_model doesn't also block the fallback attempt.
    gemini_fallback_model: str = Field(
        "gemini-3.5-flash-lite", validation_alias="ZAKI_GEMINI_FALLBACK_MODEL"
    )

    # Escalation model for complex/analytical requests. Switched from Opus to
    # Sonnet 2026-09 per user decision (cost/latency over max capability for
    # this workload). claude-3-5-sonnet-20241022 (the ID given at request
    # time) is a retired dated snapshot — claude-sonnet-5 is the current
    # Sonnet model as of this writing; verify against docs.claude.com before
    # changing again.
    claude_model: str = Field("claude-sonnet-5", validation_alias="ZAKI_CLAUDE_MODEL")

    # low | medium | high | xhigh | max — medium balances the escalation
    # path's reasoning quality against voice-reply latency.
    claude_effort: str = Field("medium", validation_alias="ZAKI_CLAUDE_EFFORT")

    # Requests longer than this many characters escalate to Claude
    # regardless of complexity-cue matching (router.py).
    escalation_length: int = Field(280, validation_alias="ZAKI_ESCALATION_LENGTH")

    # --- Google OAuth (step 2) ---------------------------------------------
    # Optional so the app still boots and the text-only chat path (step 1)
    # keeps working before Google is configured — auth_google.py raises a
    # clear error only when an OAuth endpoint is actually hit without these set.
    google_oauth_client_id: SecretStr | None = Field(
        None, validation_alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    google_oauth_client_secret: SecretStr | None = Field(
        None, validation_alias="GOOGLE_OAUTH_CLIENT_SECRET"
    )
    google_oauth_redirect_uri: str = Field(
        "http://127.0.0.1:8000/auth/google/callback",
        validation_alias="GOOGLE_OAUTH_REDIRECT_URI",
    )

    # --- Postgres / Supabase (step 3) --------------------------------------
    # DATABASE_URL: pgbouncer transaction-mode pooler — the app's runtime pool.
    # DIRECT_URL: session-mode/direct connection — migrations only (db.py).
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    direct_url: SecretStr = Field(validation_alias="DIRECT_URL")

    # Web search (step 4) needs no settings — ddgs requires no API key.

    # --- Speech-to-text (step 5) --------------------------------------------
    # Optional — the text endpoint (steps 1-4) keeps working without it; the
    # new audio endpoint returns a clear error until this is set.
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")
    # gpt-4o-transcribe supersedes whisper-1 (better WER + language
    # recognition) — verified current at build time, see stt.py.
    stt_model: str = Field("gpt-4o-transcribe", validation_alias="ZAKI_STT_MODEL")

    # --- Text-to-speech (step 7) ---------------------------------------------
    # Switched from ElevenLabs to edge-tts 2026-09-08 per user decision (no
    # paid services) — edge-tts wraps Microsoft Edge's free online TTS, no
    # API key needed at all. Switched from ar-EG-ShakirNeural (Egyptian) to
    # ar-SA-HamedNeural 2026-09-08 alongside the system prompt's move to
    # Modern Standard Arabic (فصحى) — Hamed reads فصحى text cleanly without
    # the dialect-synthesis artifacts an Egyptian voice produces on MSA
    # input. Verified against the live edge-tts voice list at build time.
    tts_voice: str = Field("ar-SA-HamedNeural", validation_alias="ZAKI_TTS_VOICE")

    # --- WhatsApp via Evolution API (self-hosted WhatsApp gateway) ---------
    # Optional — the tools/webhook return a clear "not configured" error
    # until these are set. Evolution API is a fast-moving, self-hosted
    # project (same staleness risk already flagged for ddgs/edge-tts in
    # this codebase) — verify request/response shapes against your own
    # instance's version before relying on this.
    evolution_api_url: str | None = Field(None, validation_alias="EVOLUTION_API_URL")
    evolution_api_key: SecretStr | None = Field(None, validation_alias="EVOLUTION_API_KEY")
    evolution_instance_name: str | None = Field(None, validation_alias="EVOLUTION_INSTANCE_NAME")
    # A secret YOU generate and put in the Evolution API webhook URL as
    # ?token=... when configuring it to call /api/webhooks/whatsapp —
    # Evolution's own webhook call carries no Zaki-specific auth otherwise.
    whatsapp_webhook_token: SecretStr | None = Field(None, validation_alias="WHATSAPP_WEBHOOK_TOKEN")

    # --- Phone calls via Twilio ---------------------------------------------
    # Account SID is an identifier (appears in Twilio's own dashboard URLs),
    # not a credential — the auth token is the actual secret.
    twilio_account_sid: str | None = Field(None, validation_alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: SecretStr | None = Field(None, validation_alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str | None = Field(None, validation_alias="TWILIO_PHONE_NUMBER")
    # Publicly reachable base URL for this backend (an ngrok tunnel in dev,
    # or the real deployed URL) — Twilio must fetch TwiML and open the
    # Media Stream over the public internet; 127.0.0.1 doesn't work here.
    public_base_url: str | None = Field(None, validation_alias="ZAKI_PUBLIC_BASE_URL")

    # --- Telegram bot ---------------------------------------------------------
    # Single-user allowlist, same spirit as ZAKI_API_KEY (spec §8) — anyone
    # who discovers the bot's @username can message it, so every update is
    # checked against this one user id before anything runs.
    telegram_bot_token: SecretStr | None = Field(None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: str | None = Field(None, validation_alias="TELEGRAM_ALLOWED_USER_ID")
    # Only relevant in webhook mode (requires ZAKI_PUBLIC_BASE_URL to be a
    # real https:// URL — Telegram rejects http/localhost outright, unlike
    # Evolution API). Without a public URL the bot runs in long-polling mode
    # instead, which needs no secret and no inbound URL at all. Sent back to
    # Telegram via setWebhook and checked against the X-Telegram-Bot-Api-
    # Secret-Token header on every webhook call.
    telegram_webhook_secret: SecretStr | None = Field(None, validation_alias="TELEGRAM_WEBHOOK_SECRET")

    # --- Daily briefing (APScheduler) -----------------------------------------
    # Only actually scheduled if Telegram is configured too — it's the one
    # channel in this project that can push to the user unprompted.
    daily_briefing_enabled: bool = Field(True, validation_alias="ZAKI_DAILY_BRIEFING_ENABLED")
    daily_briefing_hour: int = Field(8, validation_alias="ZAKI_DAILY_BRIEFING_HOUR")
    daily_briefing_minute: int = Field(0, validation_alias="ZAKI_DAILY_BRIEFING_MINUTE")

    # SQLite file backing APScheduler's persistent job store — reminders
    # and the daily briefing job survive a backend restart/reboot. Relative
    # default so a from-scratch checkout works with zero config; override
    # to point at a roomier drive. Forward slashes even on Windows — a
    # double-quoted backslash path in .env gets parsed for escape
    # sequences (e.g. \t becomes a literal tab), a real trap already hit
    # once building this project (see ZAKI_TEMP_DIR's history).
    reminders_db_path: str = Field("data/reminders.db", validation_alias="ZAKI_REMINDERS_DB_PATH")
    # If set, overrides reminders_db_path entirely — a full SQLAlchemy URL
    # (e.g. postgresql+psycopg2://...) used instead of SQLite. Required for
    # persistence on Render's free tier, which has no persistent disk at
    # all; a Postgres URL (the same Supabase project DATABASE_URL/DIRECT_URL
    # already point at works fine) survives redeploys and spin-down/wake
    # cycles that wipe local SQLite files. Use the DIRECT (non-pgbouncer)
    # connection string — see db.py's own comment on why pgbouncer
    # transaction mode doesn't reliably support the prepared statements
    # SQLAlchemy's default psycopg2 usage relies on.
    reminders_db_url: str | None = Field(None, validation_alias="ZAKI_REMINDERS_DB_URL")

    # Directory Python's tempfile module (and anything built on it — e.g.
    # Starlette's UploadFile, which spills large uploads to disk) should use
    # for this process. Set explicitly rather than relying solely on the
    # OS-level TEMP/TMP redirection: those only apply to freshly-started
    # processes, not ones already running when the env var changes, so this
    # guarantees Zaki's own temp files land on Y: regardless of that timing.
    zaki_temp_dir: str | None = Field(None, validation_alias="ZAKI_TEMP_DIR")


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — env is read once per process."""
    return Settings()
