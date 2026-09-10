"""Pure-Python G.711 μ-law codec.

`audioop` — the obvious stdlib choice for this — was removed in Python
3.13 (PEP 594). This project runs 3.14, so any code path that imports it
(directly, or transitively through `pydub`'s resampling, which calls
`audioop.ratecv` internally) breaks outright. Same class of "verify before
assuming it's there" risk already flagged elsewhere in this codebase for
ddgs/edge-tts/openWakeWord — caught here instead of shipped broken.

Twilio Media Streams speak exactly one format either direction:
audio/x-mulaw, 8000 Hz, mono, 20ms frames (160 bytes μ-law / 320 bytes
PCM16 per frame). That's the only conversion this module needs to do, so
it's a small, dependency-free implementation rather than pulling in a
general-purpose audio library for it.
"""

_BIAS = 0x84
_CLIP = 32635


def ulaw_to_pcm16(data: bytes) -> bytes:
    """Decode μ-law bytes to little-endian 16-bit signed PCM."""
    out = bytearray(len(data) * 2)
    for i, u in enumerate(data):
        u = ~u & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
        if sign:
            sample = -sample
        sample = max(-32768, min(32767, sample))
        out[2 * i] = sample & 0xFF
        out[2 * i + 1] = (sample >> 8) & 0xFF
    return bytes(out)


def pcm16_to_ulaw(data: bytes) -> bytes:
    """Encode little-endian 16-bit signed PCM to μ-law bytes."""
    out = bytearray(len(data) // 2)
    for i in range(0, len(data) - 1, 2):
        sample = data[i] | (data[i + 1] << 8)
        if sample >= 0x8000:
            sample -= 0x10000

        sign = 0x80 if sample < 0 else 0
        if sample < 0:
            sample = -sample
        sample = min(sample, _CLIP) + _BIAS

        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        out[i // 2] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(out)


def pcm16_rms(data: bytes) -> float:
    """Root-mean-square amplitude of little-endian 16-bit PCM — used for
    simple energy-based silence detection (see telephony.py)."""
    if len(data) < 2:
        return 0.0
    count = len(data) // 2
    total = 0
    for i in range(0, count * 2, 2):
        s = data[i] | (data[i + 1] << 8)
        if s >= 0x8000:
            s -= 0x10000
        total += s * s
    return (total / count) ** 0.5
