"""FastAPI entrypoint.

Step 1 (spec §3.3): text-only reasoning loop, testable via a simple web form.
Step 2 (spec §3.3.1): Google OAuth + Calendar/Gmail/Drive tool-calling.
Step 3 (spec §3.4): Postgres-backed context/tokens + tasks/notes tools.
Step 5 (spec §3.3): audio endpoint (STT) for the Windows client.
Step 6 (spec §3.2): combined voice endpoint for the iPhone Shortcut.
Step 7 (spec §3.3): TTS endpoint (voice output).
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from twilio.request_validator import RequestValidator

from zaki.auth import require_api_key
from zaki.auth_google import (
    get_google_credentials,
    get_token_store,
    migrate_file_token_to_postgres,
    router as google_auth_router,
)
from zaki.config import Settings, get_settings
from zaki.context import ContextProvider, get_context_provider
from zaki import dashboard as dashboard_module
from zaki.db import close_pool, get_pool, run_migrations
from zaki.pipeline import run_assistant_pipeline
from zaki.scheduler import (
    configure_scheduler,
    schedule_daily_briefing,
    schedule_temp_cleanup,
    scheduler,
    shutdown_scheduler,
    start_scheduler,
)
from zaki.schemas import AssistantRequest, AssistantResponse, TTSRequest
from zaki.stt import transcribe
from zaki import telegram as telegram_module
from zaki.tools import telephony, whatsapp
from zaki.tts import synthesize

SettingsDep = Annotated[Settings, Depends(get_settings)]
ContextDep = Annotated[ContextProvider, Depends(get_context_provider)]

logger = logging.getLogger(__name__)

# Set as early as possible, before any request can trigger a temp-file write
# (e.g. Starlette's UploadFile spilling a large audio upload to disk). Only
# applied when ZAKI_TEMP_DIR is explicitly set — no hardcoded Windows path
# here, since this same module targets a Linux/Vercel deployment too.
_zaki_temp_dir = get_settings().zaki_temp_dir
if _zaki_temp_dir:
    os.makedirs(_zaki_temp_dir, exist_ok=True)
    tempfile.tempdir = _zaki_temp_dir


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast on missing/invalid settings at startup rather than on the
    # first request.
    settings = get_settings()
    await run_migrations()
    await migrate_file_token_to_postgres()

    polling_task: asyncio.Task | None = None
    app.state.telegram_mode = "disabled"
    if settings.telegram_bot_token and settings.telegram_allowed_user_id:
        # Telegram flatly rejects setWebhook for anything but a real
        # https:// URL (unlike Evolution API, which accepted our plain
        # http://localhost for WhatsApp) — fall back to long polling
        # whenever there's no genuine public URL configured. The two are
        # mutually exclusive at Telegram's end: an active webhook makes
        # getUpdates fail with 409.
        if settings.public_base_url and settings.public_base_url.startswith("https://") and settings.telegram_webhook_secret:
            try:
                await telegram_module.register_webhook(
                    public_base_url=settings.public_base_url,
                    webhook_secret=settings.telegram_webhook_secret.get_secret_value(),
                )
                app.state.telegram_mode = "webhook"
            except RuntimeError as exc:
                logger.warning("Telegram webhook registration failed, falling back to polling: %s", exc)
                polling_task = asyncio.create_task(
                    telegram_module.run_polling_loop(settings, get_context_provider())
                )
                app.state.telegram_mode = "polling"
        else:
            polling_task = asyncio.create_task(
                telegram_module.run_polling_loop(settings, get_context_provider())
            )
            app.state.telegram_mode = "polling"
    app.state.telegram_polling_task = polling_task

    configure_scheduler(db_url=settings.reminders_db_url, db_path=settings.reminders_db_path)
    start_scheduler()
    if settings.daily_briefing_enabled and settings.telegram_bot_token and settings.telegram_allowed_user_id:
        schedule_daily_briefing(
            chat_id=int(settings.telegram_allowed_user_id),
            hour=settings.daily_briefing_hour,
            minute=settings.daily_briefing_minute,
        )
    # Always scheduled, not just when ZAKI_TEMP_DIR is explicitly set —
    # tempfile.gettempdir() reflects whatever's actually in effect (the
    # override above, or the OS default), so this covers both this
    # machine's Y:\zaki\temp and Render's /app/temp-or-wherever equally.
    schedule_temp_cleanup(temp_dir=tempfile.gettempdir())

    yield

    shutdown_scheduler()
    if polling_task is not None:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    await close_pool()


app = FastAPI(title="Zaki Assistant OS", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().zaki_frontend_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(google_auth_router)


@app.get("/health")
async def health(request: Request, settings: SettingsDep) -> Response:
    """Deep status check, not just "the process is alive" — meant for both
    human debugging and an external uptime pinger (e.g. cron-job.org)
    hitting this on a schedule to keep a free Render instance from
    spinning down. Always returns 200 (even when a sub-check is unhealthy)
    so the pinger doesn't itself start alerting on things this endpoint
    already reports in the body — check the JSON, not just the status code.

    Note on "env validation": there's no meaningful way to catch a missing
    *required* setting here — if one were actually missing, get_settings()
    would have raised at startup and this process wouldn't be running to
    serve the request at all. (An earlier version of this check read
    os.environ directly and always reported every var "missing" — pydantic-
    settings parses .env itself and never writes those values into
    os.environ, so that check was testing the wrong thing entirely.) What's
    actually useful to report is which *optional* integrations are wired
    up, since those fail silently otherwise.
    """
    db_status = "unknown"
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    try:
        scheduler_jobs = len(scheduler.get_jobs())
        scheduler_status = "running" if scheduler.running else "stopped"
    except Exception as exc:
        scheduler_jobs = 0
        scheduler_status = f"error: {exc}"

    telegram_mode = getattr(request.app.state, "telegram_mode", "disabled")
    polling_task = getattr(request.app.state, "telegram_polling_task", None)
    if telegram_mode == "polling":
        telegram_status = "running" if polling_task and not polling_task.done() else "stopped"
    elif telegram_mode == "webhook":
        telegram_status = "registered"
    else:
        telegram_status = "disabled"

    whatsapp_configured = bool(settings.evolution_api_url and settings.evolution_api_key)
    if whatsapp_configured:
        try:
            whatsapp_status = await whatsapp.get_whatsapp_connection_status()
        except Exception as exc:
            whatsapp_status = f"error: {exc}"
    else:
        whatsapp_status = "not configured"

    integrations = {
        "openai_stt_and_fallback_model": settings.openai_api_key is not None,
        "whatsapp": whatsapp_status,
        "twilio": bool(settings.twilio_account_sid and settings.twilio_auth_token),
        "google_oauth": bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
        "reminders_persistent_store": "postgres" if settings.reminders_db_url else "sqlite",
    }

    body = {
        "status": "ok",
        "database": db_status,
        "scheduler": {"status": scheduler_status, "jobs": scheduler_jobs},
        "telegram": {"mode": telegram_mode, "status": telegram_status},
        "integrations": integrations,
    }
    return Response(content=json.dumps(body, ensure_ascii=False), media_type="application/json")


@app.post(
    "/api/assistant",
    dependencies=[Depends(require_api_key)],
)
async def assistant(
    request: AssistantRequest,
    settings: SettingsDep,
    context: ContextDep,
) -> AssistantResponse:
    try:
        return await run_assistant_pipeline(
            text=request.text, session_id=request.session_id, settings=settings, context=context
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/assistant/audio",
    dependencies=[Depends(require_api_key)],
)
async def assistant_audio(
    settings: SettingsDep,
    context: ContextDep,
    audio: Annotated[UploadFile, File(description="Recorded audio, e.g. WAV")],
    session_id: Annotated[str, Form()] = "default",
) -> AssistantResponse:
    """Windows client path (spec §3.3): raw audio in, STT, then the same
    pipeline as the text endpoint. iPhone's Shortcuts flow dictates text
    client-side and skips this endpoint entirely.
    """
    audio_bytes = await audio.read()
    try:
        text = await transcribe(audio_bytes, filename=audio.filename or "audio.wav")
        return await run_assistant_pipeline(
            text=text, session_id=session_id, settings=settings, context=context
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/assistant/voice",
    dependencies=[Depends(require_api_key)],
)
async def assistant_voice(
    settings: SettingsDep,
    context: ContextDep,
    audio: Annotated[UploadFile | None, File(description="Recorded audio, e.g. m4a/wav")] = None,
    text: Annotated[str | None, Form(description="Already-dictated text; alternative to audio")] = None,
    session_id: Annotated[str, Form()] = "default",
    voice: Annotated[str | None, Form(description="Overrides ZAKI_TTS_VOICE")] = None,
) -> Response:
    """iPhone Shortcuts path (spec §3.2): one call in, spoken reply out —
    collapses transcribe -> reason -> synthesize into a single round-trip
    so the Shortcut itself stays to ~3 actions (dictate-or-record -> Get
    Contents of URL -> Play Sound) instead of chaining multiple API calls.

    Accepts EITHER `audio` (raw recording, transcribed server-side) OR
    `text` (already dictated on-device via Shortcuts' own Dictate Text
    action, skipping STT) — matches spec §3.2's "record/dictate -> HTTP
    POST audio or dictated text" exactly.

    Response body is the spoken reply (audio/mpeg); the reply TEXT rides
    along in a response header (`X-Zaki-Reply-B64`, base64-encoded since
    HTTP headers aren't UTF-8-safe) for Shortcuts that also want to show
    or log it, e.g. via "Get Dictionary from Input" isn't applicable here —
    read the header directly with "Get Details of Web Response" on iOS 17+.
    """
    try:
        if audio is not None:
            audio_bytes = await audio.read()
            input_text = await transcribe(audio_bytes, filename=audio.filename or "audio.m4a")
        elif text is not None and text.strip():
            input_text = text
        else:
            raise HTTPException(status_code=422, detail="Provide either 'audio' or 'text'")

        result = await run_assistant_pipeline(
            text=input_text, session_id=session_id, settings=settings, context=context
        )
        speech = await synthesize(result.reply, voice=voice)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    reply_b64 = base64.b64encode(result.reply.encode("utf-8")).decode("ascii")
    return Response(
        content=speech,
        media_type="audio/mpeg",
        headers={
            "X-Zaki-Reply-B64": reply_b64,
            "X-Zaki-Route": result.route,
            "X-Zaki-Model": result.model,
        },
    )


@app.get(
    "/api/dashboard",
    dependencies=[Depends(require_api_key)],
)
async def dashboard() -> dict:
    """Read-only snapshot for the web frontend's dashboard widgets (tasks,
    notes, calendar, email). Polled on load and after each chat turn so a
    tool call Zaki just made (e.g. create_task) is reflected without a
    manual refresh.
    """
    try:
        google_credentials = await get_google_credentials(get_token_store())
    except Exception:
        # Same failure mode as pipeline.py's identical try/except: an
        # expired token with a revoked/invalid refresh_token makes
        # creds.refresh() raise google.auth.exceptions.RefreshError, which
        # used to propagate straight out of this endpoint as a bare 500 —
        # exactly the "loading widgets" crash this was filed against.
        # Degrade to "not connected" instead; calendar/emails below
        # already treat google_credentials=None as the normal
        # not-linked-yet state.
        logger.exception("Google credentials load/refresh failed in /api/dashboard")
        google_credentials = None

    tasks = await dashboard_module.get_tasks()
    notes = await dashboard_module.get_notes()

    try:
        calendar_events = await dashboard_module.get_calendar_events(google_credentials)
    except Exception:
        calendar_events = None
    try:
        emails = await dashboard_module.get_recent_emails(google_credentials)
    except Exception:
        emails = None

    return {
        "tasks": tasks,
        "notes": notes,
        "calendar": calendar_events,
        "emails": emails,
        "google_connected": google_credentials is not None,
    }


@app.post(
    "/api/tts",
    dependencies=[Depends(require_api_key)],
)
async def tts(request: TTSRequest) -> Response:
    """Separate from the assistant endpoints on purpose: not every client
    wants audio (the web test form doesn't need to auto-play), so callers
    fetch text first, then opt in to synthesis for this specific reply.
    """
    try:
        audio = await synthesize(request.text, voice=request.voice)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    settings: SettingsDep,
    context: ContextDep,
    token: str | None = None,
) -> dict:
    """Evolution API calls this when a WhatsApp message arrives. Configure
    the webhook URL on your Evolution instance as
    `{PUBLIC_BASE_URL}/api/webhooks/whatsapp?token=<WHATSAPP_WEBHOOK_TOKEN>`
    — Evolution's own call carries no Zaki-specific auth otherwise, so this
    query-param token is what stands in for it.
    """
    if settings.whatsapp_webhook_token is None or token != settings.whatsapp_webhook_token.get_secret_value():
        raise HTTPException(status_code=401, detail="Missing or invalid webhook token")

    payload = await request.json()
    parsed = whatsapp.parse_evolution_webhook(payload)
    if parsed is None:
        return {"status": "ignored"}

    from_number, text = parsed
    try:
        result = await run_assistant_pipeline(
            text=text, session_id=f"whatsapp:{from_number}", settings=settings, context=context
        )
        await whatsapp.send_whatsapp_text(from_number, result.reply)
    except RuntimeError as exc:
        logger.warning("WhatsApp webhook failed for %s: %s", from_number, exc)
        # A failure here shouldn't surface as a 500 back to Evolution (it
        # would just retry the same webhook delivery); the sender simply
        # doesn't get a reply this time.
        return {"status": "error"}
    return {"status": "ok"}


@app.get("/api/whatsapp/qr")
async def whatsapp_qr(settings: SettingsDep, key: str | None = None) -> Response:
    """Serves the most recent WhatsApp reconnect QR code (populated by the
    reconnect_whatsapp_session tool) as a plain PNG image. Meant to be
    opened directly in a phone's browser to scan — a header-based API key
    can't be attached from a tapped link, so this uses the same query-
    param-token convention as the WhatsApp webhook above instead of
    require_api_key.
    """
    if key != settings.zaki_api_key.get_secret_value():
        raise HTTPException(status_code=401, detail="Missing or invalid key")

    qr = whatsapp.get_cached_qr_base64()
    if qr is None:
        raise HTTPException(
            status_code=404,
            detail="لا يوجد رمز QR متاح حاليًا — اطلب من زكي إعادة الاتصال أولاً",
        )

    if "," in qr:
        qr = qr.split(",", 1)[1]
    image_bytes = base64.b64decode(qr)
    return Response(content=image_bytes, media_type="image/png")


@app.post("/api/webhooks/twilio/voice")
async def twilio_voice_webhook(request: Request, settings: SettingsDep) -> Response:
    """Twilio fetches this the moment a call connects (both for calls Zaki
    places via place_pending_call, and any inbound call to the Twilio
    number). Validates Twilio's request signature so this can't be spoofed
    by a third party who finds the URL.
    """
    if settings.twilio_auth_token and settings.public_base_url:
        form = await request.form()
        validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
        url = f"{settings.public_base_url.rstrip('/')}/api/webhooks/twilio/voice"
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(url, dict(form), signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    twiml = telephony.build_voice_twiml(settings.public_base_url or "")
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/api/webhooks/twilio/voice/stream")
async def twilio_voice_stream(websocket: WebSocket) -> None:
    """Twilio Media Streams connects here for the duration of the call —
    see telephony.handle_media_stream for the actual audio bridge."""
    await telephony.handle_media_stream(websocket)


@app.post("/api/webhooks/telegram")
async def telegram_webhook(request: Request, settings: SettingsDep, context: ContextDep) -> dict:
    """Only reachable when the bot is actually running in webhook mode
    (see lifespan) — otherwise Telegram is never told this URL exists.
    Validates the secret Telegram echoes back on every call, set via
    setWebhook's secret_token and checked here against the
    X-Telegram-Bot-Api-Secret-Token header, per Telegram's own mechanism
    for this (not a bespoke query-param scheme like the WhatsApp webhook
    uses, since Telegram supports this natively).
    """
    if (
        settings.telegram_webhook_secret is None
        or request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        != settings.telegram_webhook_secret.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="Missing or invalid webhook secret")

    payload = await request.json()
    await telegram_module.handle_update(payload, settings=settings, context=context)
    return {"ok": True}


# Serve the plain test form (spec step 1: "testable via a simple web form").
# app.frontend() is the FastAPI-preferred helper for a built/static
# directory; fall back to a manual static mount if unavailable in the
# pinned FastAPI version.
try:
    app.frontend("/", directory="static")
except AttributeError:
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory="static", html=True), name="static")
