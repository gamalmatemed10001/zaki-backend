"""Speech-to-text — spec §3.3: "STT: Whisper API (for Windows raw-audio
path; iPhone may send already-dictated text via Siri, skipping this step)."

Uses gpt-4o-transcribe rather than whisper-1: OpenAI's current guidance is
that whisper-1 is the legacy model (224-token prompt limit, no streaming);
gpt-4o-transcribe has better WER and language recognition — verified at
build time, not assumed from training data (same staleness risk as every
other vendor model ID in this project).
"""

from openai import AsyncOpenAI

from zaki.config import get_settings


async def transcribe(audio_bytes: bytes, *, filename: str = "audio.wav") -> str:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise RuntimeError("Speech-to-text isn't configured yet — set OPENAI_API_KEY")

    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    try:
        result = await client.audio.transcriptions.create(
            model=settings.stt_model,
            file=(filename, audio_bytes),
        )
    except Exception as exc:  # openai SDK's exception hierarchy varies by error type
        raise RuntimeError(f"Transcription failed: {exc}") from exc

    text = result.text.strip()
    if not text:
        raise RuntimeError("Transcription returned empty text — audio may be silent or unclear")
    return text
