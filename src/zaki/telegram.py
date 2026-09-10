"""Telegram bot channel — text and voice notes in, Zaki's reply out.

Unlike whatsapp.py's send_pending_whatsapp_message, this has no draft/
confirm gate: replying to an inbound Telegram message is Zaki talking back
to the one person who reached it (locked to TELEGRAM_ALLOWED_USER_ID
below), not sending an unsolicited message to a third party — the same
category as the iPhone Shortcut's /api/assistant/voice endpoint, not the
same category as gmail.py/whatsapp.py's send tools.

Two transports share the same update-handling logic (handle_update below):

- Long polling (run_polling_loop): calls getUpdates in a loop. Needs no
  public URL at all — this is what runs when ZAKI_PUBLIC_BASE_URL isn't a
  real https:// address, which is the common case in local dev.
- Webhook (/api/webhooks/telegram in main.py): Telegram POSTs updates to
  us. Requires setWebhook, which Telegram flatly rejects for anything but
  a real https:// URL (unlike Evolution API, which accepted our plain
  http://localhost:8000 for the WhatsApp webhook) — so this only activates
  once ZAKI_PUBLIC_BASE_URL is a genuine public HTTPS address.

These two are mutually exclusive at the Telegram API level (a bot with an
active webhook gets HTTP 409 from getUpdates) — main.py's lifespan picks
exactly one based on whether a usable public URL is configured.
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from zaki.config import Settings, get_settings
from zaki.context import ContextProvider
from zaki.stt import transcribe
from zaki.tts import synthesize

logger = logging.getLogger(__name__)

_POLL_TIMEOUT = 30  # seconds — Telegram long-polls the connection open this long

# --- Rate limiting / input hygiene -----------------------------------------
# This is a single-user bot, so this isn't really abuse prevention — it's a
# guard against Telegram's own retry/double-delivery quirks (a slow reply
# can make Telegram redeliver the same update) and against accidentally
# pasting something huge into the chat.
_RATE_LIMIT_WINDOW_SECONDS = 10
_RATE_LIMIT_MAX_MESSAGES = 5
_MAX_INPUT_CHARS = 4000

_recent_message_times: list[float] = []

# Last assistant reply per chat, so a short "اسمعني" follow-up can request
# the voice version of a reply that was originally sent as text (long
# replies aren't auto-voiced — see _maybe_add_listen_hint below).
_last_reply_by_chat: dict[int | str, str] = {}

_LISTEN_TRIGGER_PHRASES = ("اسمعني", "اسمعنى", "اقرأها بصوت", "شغلها صوت")
_LONG_REPLY_CHAR_THRESHOLD = 400


def _is_rate_limited() -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    while _recent_message_times and _recent_message_times[0] < cutoff:
        _recent_message_times.pop(0)
    if len(_recent_message_times) >= _RATE_LIMIT_MAX_MESSAGES:
        return True
    _recent_message_times.append(now)
    return False


def _truncate_input(text: str) -> str:
    if len(text) <= _MAX_INPUT_CHARS:
        return text
    logger.warning("Truncating oversized Telegram input (%d chars)", len(text))
    return text[:_MAX_INPUT_CHARS]


async def alert_admin(message: str) -> None:
    """Pushes a system alert to the same Telegram chat this whole bot is
    locked to — used for genuinely unexpected errors (not the routine
    "a provider tier failed, cascading" case, which is already handled
    gracefully and isn't alert-worthy). Never raises: an alert failing
    must not itself become a second incident, and must not recurse if the
    thing that broke IS Telegram delivery.
    """
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_allowed_user_id):
        return
    try:
        await send_telegram_message(int(settings.telegram_allowed_user_id), f"⚠️ تنبيه نظام زكي:\n{message}")
    except Exception:
        logger.exception("Failed to deliver admin alert (message was: %s)", message)


def _require_config(settings: Settings) -> tuple[str, str]:
    if not (settings.telegram_bot_token and settings.telegram_allowed_user_id):
        raise RuntimeError(
            "Telegram isn't configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID"
        )
    return settings.telegram_bot_token.get_secret_value(), settings.telegram_allowed_user_id


def _api_base(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}"


async def send_telegram_message(chat_id: int | str, text: str) -> None:
    settings = get_settings()
    bot_token, _ = _require_config(settings)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{_api_base(bot_token)}/sendMessage", json={"chat_id": chat_id, "text": text})
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram sendMessage error {resp.status_code}: {resp.text[:300]}")


async def _mp3_to_ogg_opus(mp3_bytes: bytes) -> bytes:
    """Telegram's sendVoice requires OGG/Opus specifically to render as a
    native voice-note bubble (waveform + play button) rather than a plain
    audio-file attachment — edge-tts only outputs MP3, so this shells out
    to ffmpeg for the conversion. Same ffmpeg-over-pydub reasoning as
    telephony.py: pydub's resampling depends on the stdlib `audioop`
    module, removed in Python 3.13+ (this project runs 3.14).
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "32k",
        "-f", "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=mp3_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3->ogg/opus conversion failed: {stderr.decode(errors='ignore')[:300]}")
    return stdout


async def send_telegram_voice(chat_id: int | str, text: str, voice: str | None = None) -> None:
    """Synthesizes `text` with edge-tts (free, no API key) and sends it as
    a native Telegram voice note. Requires `ffmpeg` on PATH — same
    operational prerequisite already documented for the Twilio bridge.
    """
    bot_token, _ = _require_config(get_settings())
    mp3_bytes = await synthesize(text, voice=voice)
    ogg_bytes = await _mp3_to_ogg_opus(mp3_bytes)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_api_base(bot_token)}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": ("reply.ogg", ogg_bytes, "audio/ogg")},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram sendVoice error {resp.status_code}: {resp.text[:300]}")


async def _download_voice(bot_token: str, file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        file_resp = await client.get(f"{_api_base(bot_token)}/getFile", params={"file_id": file_id})
        if file_resp.status_code >= 400:
            raise RuntimeError(f"Telegram getFile error {file_resp.status_code}: {file_resp.text[:300]}")
        file_path = file_resp.json()["result"]["file_path"]

        download_resp = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
        if download_resp.status_code >= 400:
            raise RuntimeError(f"Telegram file download error {download_resp.status_code}")
        return download_resp.content


def parse_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extracts the bits handle_update needs from a raw Telegram Update, or
    None for update types we don't handle (edited messages, channel posts,
    callback queries, etc). Voice notes arrive as OGG/Opus — verify that
    format stays accepted by whichever OpenAI transcription model
    ZAKI_STT_MODEL points to; this hasn't been independently reverified
    against live docs at write time (same discipline as other vendor
    format assumptions in this codebase).
    """
    message = payload.get("message")
    if not isinstance(message, dict):
        return None

    from_user = message.get("from") or {}
    user_id = from_user.get("id")
    chat_id = (message.get("chat") or {}).get("id")
    if user_id is None or chat_id is None:
        return None

    voice = message.get("voice") or message.get("audio")
    return {
        "user_id": str(user_id),
        "chat_id": chat_id,
        "text": message.get("text"),
        "voice_file_id": voice.get("file_id") if voice else None,
    }


async def handle_update(payload: dict[str, Any], *, settings: Settings, context: ContextProvider) -> None:
    bot_token, allowed_user_id = _require_config(settings)

    parsed = parse_update(payload)
    if parsed is None:
        return

    # Strict authorization at the entry point: rejected before touching the
    # LLM pipeline, database, or any API budget — just a log line naming
    # the offending id.
    if parsed["user_id"] != allowed_user_id:
        logger.warning("Ignoring Telegram message from unauthorized user_id=%s", parsed["user_id"])
        return

    chat_id = parsed["chat_id"]
    is_voice_input = bool(parsed["voice_file_id"])

    if _is_rate_limited():
        logger.warning("Rate limit hit for chat_id=%s — dropping this update", chat_id)
        return  # no reply at all: acknowledging a flood just encourages more of it

    if is_voice_input:
        try:
            audio_bytes = await _download_voice(bot_token, parsed["voice_file_id"])
            text = _truncate_input(await transcribe(audio_bytes, filename="voice.ogg"))
        except RuntimeError as exc:
            logger.warning("Telegram voice transcription failed: %s", exc)
            await send_telegram_message(chat_id, "عذرًا، تعذّر فهم الرسالة الصوتية. جرّب إرسالها مرة أخرى.")
            return
    elif parsed["text"]:
        text = _truncate_input(parsed["text"])
    else:
        return  # sticker/photo/etc — nothing to feed the pipeline

    # "اسمعني" follow-up: speak the last reply instead of running a fresh
    # turn through the LLM at all.
    if any(phrase in text for phrase in _LISTEN_TRIGGER_PHRASES):
        last_reply = _last_reply_by_chat.get(chat_id)
        if last_reply:
            try:
                await send_telegram_voice(chat_id, last_reply)
            except RuntimeError as exc:
                logger.warning("Telegram listen-trigger voice failed: %s", exc)
                await send_telegram_message(chat_id, last_reply)
        else:
            await send_telegram_message(chat_id, "لا يوجد ردّ سابق لأقرأه لك بعد.")
        return

    # Deferred import: same reasoning as telephony.py — zaki.pipeline pulls
    # in zaki.providers.base -> zaki.tools.base, which forces zaki.tools'
    # package __init__ to run; a top-level import here isn't circular
    # (zaki.tools never imports zaki.telegram), but keeping the import
    # style consistent with the other channel modules avoids surprises if
    # that ever changes.
    from zaki.pipeline import run_assistant_pipeline

    try:
        result = await run_assistant_pipeline(
            text=text, session_id=f"telegram:{chat_id}", settings=settings, context=context
        )
        _last_reply_by_chat[chat_id] = result.reply

        # Adaptive voice routing: a voice note in gets a voice note back
        # (edge-tts, free); short text in gets text back; a long reply to
        # a text message stays text (voice-ifying a long report is often
        # annoying to sit through) but offers a one-word way to hear it.
        if is_voice_input:
            try:
                await send_telegram_voice(chat_id, result.reply)
            except RuntimeError as exc:
                logger.warning("Telegram voice reply failed, falling back to text: %s", exc)
                await send_telegram_message(chat_id, result.reply)
        else:
            reply_text = result.reply
            if len(reply_text) > _LONG_REPLY_CHAR_THRESHOLD:
                reply_text += "\n\n(اكتب \"اسمعني\" إذا حبيت تسمع هذا الردّ بصوت)"
            await send_telegram_message(chat_id, reply_text)
    except RuntimeError as exc:
        # Every provider tier in the cascade already failed, or STT/some
        # other RuntimeError-raising step did. This is a "graceful
        # degradation" case, not a "the app is broken" case — log the real
        # error server-side, but never forward raw exception text (often
        # English, sometimes a raw vendor JSON blob) to the chat.
        logger.warning("Telegram pipeline failed for chat_id=%s: %s", chat_id, exc)
        try:
            await send_telegram_message(
                chat_id, "عذرًا، واجهت مشكلة تقنية مؤقتة ولم أستطع الرد الآن. حاول مرة أخرى بعد قليل."
            )
        except RuntimeError:
            pass
    except Exception as exc:  # a genuine bug, not a handled provider/API failure
        logger.exception("Unexpected error handling Telegram update for chat_id=%s", chat_id)
        await alert_admin(f"خطأ غير متوقع في معالجة رسالة تيليجرام: {exc}")
        try:
            await send_telegram_message(chat_id, "عذرًا، حدث خطأ غير متوقع. تم إبلاغ المطوّر.")
        except RuntimeError:
            pass


async def register_webhook(*, public_base_url: str, webhook_secret: str) -> None:
    settings = get_settings()
    bot_token, _ = _require_config(settings)
    url = f"{public_base_url.rstrip('/')}/api/webhooks/telegram"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_api_base(bot_token)}/setWebhook",
            json={"url": url, "secret_token": webhook_secret, "allowed_updates": ["message"]},
        )
    if resp.status_code >= 400 or not resp.json().get("ok"):
        raise RuntimeError(f"Telegram setWebhook failed: {resp.text[:300]}")
    logger.info("Telegram webhook registered at %s", url)


async def run_polling_loop(settings: Settings, context: ContextProvider) -> None:
    """Long-polls getUpdates forever. Cancelled from main.py's lifespan
    shutdown. A bad update or a transient network error is logged and
    skipped rather than killing the loop — this is meant to run
    unattended for the life of the process.
    """
    bot_token, _ = _require_config(settings)
    offset: int | None = None

    logger.info("Telegram long-polling started")
    async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 10) as client:
        while True:
            try:
                params: dict[str, Any] = {"timeout": _POLL_TIMEOUT}
                if offset is not None:
                    params["offset"] = offset
                resp = await client.get(f"{_api_base(bot_token)}/getUpdates", params=params)
                resp.raise_for_status()
                updates = resp.json().get("result", [])

                for update in updates:
                    offset = update["update_id"] + 1
                    try:
                        await handle_update(update, settings=settings, context=context)
                    except Exception:
                        logger.exception("Error handling Telegram update %s", update.get("update_id"))
            except asyncio.CancelledError:
                logger.info("Telegram long-polling stopped")
                raise
            except Exception:
                logger.exception("Telegram getUpdates request failed, retrying in 5s")
                await asyncio.sleep(5)
