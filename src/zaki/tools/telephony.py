"""Outbound phone calls via Twilio, plus the live Media Streams bridge that
handles a call's audio in real time.

Placing a call to a real person is at least as intrusive as sending an
email/WhatsApp message, so it uses the same structural two-turn
confirmation gate as gmail.py/whatsapp.py — see gmail.py's module
docstring for the full rationale (place_pending_call only succeeds if the
call was staged on a *previous* turn_id).

The live audio bridge (TwiML + WebSocket, wired up in main.py) is a first
pass, not a fully streaming low-latency pipeline: it buffers each side's
speech, waits for a short silence gap to call it "done talking," then runs
transcribe -> assistant pipeline -> synthesize -> speak back. Sub-second
conversational latency (streaming ASR, barge-in handling, jitter
buffering) is a substantially bigger project — flagged here rather than
silently claimed as done.

Requires the `ffmpeg` binary on PATH (used to decode/resample edge-tts's
MP3 output down to the 8kHz mono PCM Twilio expects) — not a Python
dependency, a real operational prerequisite.
"""

import asyncio
import base64
import io
import json
import wave
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from twilio.rest import Client as TwilioClient

from zaki.audio_codec import pcm16_rms, pcm16_to_ulaw, ulaw_to_pcm16
from zaki.config import get_settings
from zaki.context import get_context_provider
from zaki.stt import transcribe
from zaki.tools.base import ToolContext, ToolDefinition
from zaki.tts import synthesize

# Twilio Media Streams: 8kHz mono μ-law, one 20ms (160-byte) frame per
# "media" event.
_FRAME_MS = 20
_SILENCE_RMS_THRESHOLD = 400  # empirical cutoff for 16-bit PCM background noise
_SILENCE_MS_TO_END_TURN = 900
_MAX_TURN_MS = 15_000  # safety cap so a stuck-open mic can't buffer forever


def _twilio_client() -> TwilioClient:
    settings = get_settings()
    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_phone_number):
        raise RuntimeError(
            "Phone calls aren't configured — set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER"
        )
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value())


@dataclass
class PendingCall:
    to: str
    objective: str
    created_turn_id: str


# Same per-session staging pattern as gmail.py/whatsapp.py.
_pending_calls: dict[str, PendingCall] = {}

# call_sid -> objective, populated once a call is actually placed; read by
# the Media Stream WebSocket handler when that call's "start" event
# arrives, so the live bridge knows what Zaki is calling about.
_active_call_objectives: dict[str, str] = {}


async def stage_phone_call(args: dict[str, Any], context: ToolContext) -> str:
    _pending_calls[context.session_id] = PendingCall(
        to=args["to_phone_number"],
        objective=args["script_or_objective"],
        created_turn_id=context.turn_id,
    )
    return (
        f"Call staged (NOT placed). To: {args['to_phone_number']}\n"
        f"Objective: {args['script_or_objective']}\n"
        "Read this back to the user and wait for their explicit "
        "confirmation on their NEXT message before ever calling "
        "place_pending_call — do not call it in this same turn; it will "
        "be rejected if you do."
    )


async def place_pending_call(args: dict[str, Any], context: ToolContext) -> str:
    pending = _pending_calls.get(context.session_id)
    if pending is None:
        return "Error: no pending call for this session. Stage one first with stage_phone_call."

    if pending.created_turn_id == context.turn_id:
        return (
            "Error: this call was staged earlier in the current turn — no "
            "human has confirmed it yet. Read it back to the user and "
            "STOP. Only call place_pending_call again after their next "
            "message explicitly confirms."
        )

    settings = get_settings()
    if not settings.public_base_url:
        return (
            "Error: ZAKI_PUBLIC_BASE_URL isn't set — Twilio needs a public "
            "URL to fetch call instructions from this server."
        )

    try:
        client = _twilio_client()
        call = await asyncio.to_thread(
            client.calls.create,
            to=pending.to,
            from_=settings.twilio_phone_number,
            url=f"{settings.public_base_url.rstrip('/')}/api/webhooks/twilio/voice",
        )
    except Exception as exc:
        return f"Error placing call: {exc}"

    _active_call_objectives[call.sid] = pending.objective
    del _pending_calls[context.session_id]
    return f"Calling {pending.to} now (call id: {call.sid})."


def build_voice_twiml(public_base_url: str) -> str:
    """TwiML Twilio fetches the moment the call connects — hands the audio
    off to our WebSocket for the live bridge rather than using <Say>/<Gather>,
    since we need full real-time control over both directions of audio.
    """
    ws_url = (
        public_base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{ws_url}/api/webhooks/twilio/voice/stream" />'
        "</Connect></Response>"
    )


async def _ffmpeg_mp3_to_pcm16_8k_mono(mp3_bytes: bytes) -> bytes:
    """Shells out to ffmpeg for decode+resample rather than pydub: pydub's
    set_frame_rate() calls into `audioop.ratecv`, which no longer exists in
    Python 3.13+ (see audio_codec.py's docstring) — ffmpeg sidesteps that
    entirely and needs no Python audio dependency at all.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "s16le",
        "-ar", "8000",
        "-ac", "1",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=mp3_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode(errors='ignore')[:300]}")
    return stdout


def _pcm16_to_wav(pcm16: bytes, *, sample_rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16)
    return buf.getvalue()


async def _speak_into_call(websocket: WebSocket, stream_sid: str, text: str, voice: str | None = None) -> None:
    """Synthesizes `text` and streams it back to Twilio as real-time-paced
    20ms μ-law frames — the same event shape Twilio sends inbound, mirrored
    outbound.
    """
    mp3 = await synthesize(text, voice=voice)
    pcm16 = await _ffmpeg_mp3_to_pcm16_8k_mono(mp3)
    ulaw = pcm16_to_ulaw(pcm16)

    frame_bytes = 160  # 20ms of 8kHz μ-law
    for i in range(0, len(ulaw), frame_bytes):
        frame = ulaw[i : i + frame_bytes]
        await websocket.send_text(
            json.dumps(
                {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": base64.b64encode(frame).decode("ascii")},
                }
            )
        )
        await asyncio.sleep(_FRAME_MS / 1000)


async def handle_media_stream(websocket: WebSocket) -> None:
    """Twilio Media Streams WebSocket handler — one connection per call leg.

    Protocol: https://www.twilio.com/docs/voice/media-streams/websocket-messages
    (verify against Twilio's current docs; this is a fast-moving product
    surface). Messages are JSON: {"event": "connected"|"start"|"media"|"stop", ...}.
    """
    await websocket.accept()

    # Deferred: zaki.tools' package __init__ imports this module, and
    # zaki.pipeline imports zaki.providers.base, which imports
    # zaki.tools.base — importing the *package* zaki.tools along the way.
    # A top-level import here would be circular; by call time (well after
    # app startup, when a real call connects) everything is fully loaded.
    from zaki.pipeline import run_assistant_pipeline

    settings = get_settings()
    context = get_context_provider()

    stream_sid: str | None = None
    call_sid: str | None = None
    objective = ""

    turn_buffer = bytearray()
    silence_ms = 0
    speaking_ms = 0

    async def process_turn() -> None:
        nonlocal turn_buffer
        if not turn_buffer or stream_sid is None:
            return
        pcm16 = bytes(turn_buffer)
        turn_buffer = bytearray()

        try:
            wav_bytes = _pcm16_to_wav(pcm16)
            user_text = await transcribe(wav_bytes, filename="call.wav")
            result = await run_assistant_pipeline(
                text=user_text,
                session_id=f"call:{call_sid}",
                settings=settings,
                context=context,
            )
            await _speak_into_call(websocket, stream_sid, result.reply)
        except RuntimeError:
            # STT/LLM/TTS hiccup mid-call — say so instead of going silent,
            # which would just read as a dropped call to the other person.
            try:
                await _speak_into_call(
                    websocket, stream_sid, "عذرًا، حدث خلل تقني. هل يمكنك إعادة ذلك؟"
                )
            except RuntimeError:
                pass

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            kind = event.get("event")

            if kind == "start":
                start = event.get("start", {})
                stream_sid = start.get("streamSid")
                call_sid = start.get("callSid")
                objective = _active_call_objectives.pop(call_sid, "") if call_sid else ""
                if objective and stream_sid:
                    # Outbound call Zaki placed — open with the objective
                    # rather than sitting silently waiting for the other
                    # side to speak first.
                    opening = await run_assistant_pipeline(
                        text=(
                            f"[بدء مكالمة هاتفية صادرة] الهدف من المكالمة: {objective}. "
                            "ابدأ المكالمة بترحيب موجز ثم اذكر سبب الاتصال."
                        ),
                        session_id=f"call:{call_sid}",
                        settings=settings,
                        context=context,
                    )
                    await _speak_into_call(websocket, stream_sid, opening.reply)

            elif kind == "media" and stream_sid is not None:
                payload = event.get("media", {}).get("payload", "")
                ulaw_frame = base64.b64decode(payload)
                pcm16_frame = ulaw_to_pcm16(ulaw_frame)
                level = pcm16_rms(pcm16_frame)

                if level >= _SILENCE_RMS_THRESHOLD:
                    turn_buffer.extend(pcm16_frame)
                    speaking_ms += _FRAME_MS
                    silence_ms = 0
                elif speaking_ms > 0:
                    turn_buffer.extend(pcm16_frame)
                    silence_ms += _FRAME_MS

                if speaking_ms > 0 and (
                    silence_ms >= _SILENCE_MS_TO_END_TURN or speaking_ms >= _MAX_TURN_MS
                ):
                    speaking_ms = 0
                    silence_ms = 0
                    await process_turn()

            elif kind == "stop":
                break
    except WebSocketDisconnect:
        pass


TOOLS = [
    ToolDefinition(
        name="stage_phone_call",
        description=(
            "Stage an outbound phone call to a phone number with an "
            "objective/script. Does NOT place the call. Always read the "
            "objective back to the user and wait for their next message "
            "to explicitly confirm before ever calling place_pending_call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to_phone_number": {
                    "type": "string",
                    "description": "Phone number with country code, e.g. +201234567890",
                },
                "script_or_objective": {
                    "type": "string",
                    "description": "What Zaki should accomplish or say on the call",
                },
            },
            "required": ["to_phone_number", "script_or_objective"],
            "additionalProperties": False,
        },
        handler=stage_phone_call,
        requires_google=False,
    ),
    ToolDefinition(
        name="place_pending_call",
        description=(
            "Place the most recently staged phone call for this session. "
            "Only call this after the user has explicitly confirmed, in "
            "their own separate message, that they want it placed. Calling "
            "it in the same turn as stage_phone_call will be rejected."
        ),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=place_pending_call,
        requires_google=False,
    ),
]
