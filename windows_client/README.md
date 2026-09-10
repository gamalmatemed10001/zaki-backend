# Zaki Windows Client

Push-to-talk tray app. Press the hotkey, speak, Zaki replies as a Windows
notification and speaks the reply aloud (edge-tts, ar-EG-ShakirNeural).

**Why push-to-talk, not always-listening wake word:** the spec's chosen
engine, openWakeWord, is confirmed English-only — its training pipeline
generates synthetic audio via English-only TTS models, with no supported
path to a `"فوق يا زكي"` model. Hotkey-based push-to-talk is the same
pragmatic fallback the spec already accepted for the iPhone client.
Hands-free wake-word can come later behind a genuinely Arabic-capable
engine (Vosk-based keyword spotting, or Picovoice Porcupine).

## Setup

```bash
cd windows_client
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set `ZAKI_API_KEY` to the same value as the backend's
`ZAKI_API_KEY`. Make sure the backend (`zaki-assistant-os`, the FastAPI
app) is running and reachable at `ZAKI_BACKEND_URL` (default
`http://127.0.0.1:8000`), and that it has `OPENAI_API_KEY` set — audio
transcription happens server-side. Text-to-speech (edge-tts) needs no key.

## Run

```bash
python tray_app.py
```

A tray icon appears (grey = idle). Press **Ctrl+Shift+Z** (or whatever
`ZAKI_HOTKEY` is set to), speak, and stop talking — recording ends
automatically after ~1.2s of silence (or after 15s regardless). Icon states:
red while recording, amber while waiting on Zaki, blue while the reply
plays aloud, then back to grey. The reply also shows as a Windows
notification alongside the spoken audio.

Right-click the tray icon for **Start with Windows** (writes to your
per-user registry Run key — nothing system-wide, no admin needed) and
**Quit**.

## Known limitations (this step)

- The `keyboard` library's global hook can be blocked by some antivirus/EDR
  software or need elevation in locked-down environments — if the hotkey
  doesn't fire, try running as Administrator once to confirm that's the
  cause before troubleshooting further.
- Playback (`playsound3`, via Windows' native MCI API) uses whatever your
  system's default audio output device is at the moment each reply plays —
  no restart needed to pick up a device change, unlike some audio libraries.
