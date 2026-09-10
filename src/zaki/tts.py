"""Text-to-speech — spec §3.3 (originally ElevenLabs). Switched to
`edge-tts` 2026-09-08 per user decision: no paid services. edge-tts wraps
Microsoft Edge's free online neural TTS — no API key, no account, no cost.

Voice: ar-EG-ShakirNeural (Egyptian Arabic, male) — confirmed present in
the live `edge_tts.list_voices()` output at build time, not assumed from
training data (same discipline as every other vendor-name decision in this
project). ar-EG-SalmaNeural (female) is the only other Egyptian option
currently offered.
"""

import edge_tts

from zaki.config import get_settings


async def synthesize(text: str, *, voice: str | None = None) -> bytes:
    settings = get_settings()
    selected_voice = voice or settings.tts_voice

    communicate = edge_tts.Communicate(text, selected_voice)
    chunks = []
    try:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
    except Exception as exc:  # edge-tts talks to an unofficial endpoint — can fail transiently
        raise RuntimeError(f"TTS synthesis failed: {exc}") from exc

    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("edge-tts returned empty audio")
    return audio
