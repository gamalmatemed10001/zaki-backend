"""Zaki Windows Client — system tray app with a global-hotkey trigger.

Push-to-talk via hotkey rather than always-on wake word: openWakeWord (the
engine the spec named) is confirmed English-only at build time — its
synthetic training pipeline is built entirely on English TTS models, with
no path to a "فوق يا زكي" model. This is the pragmatic V1 fallback the
spec already accepted for the iPhone client (§3.2: push-to-talk, no wake
word) extended to Windows, per project decision 2026-09-08. Hands-free
wake-word can come later once a real Arabic-capable engine is chosen.

Icon states: idle (grey) -> listening (red, recording) -> thinking (amber,
waiting on the backend) -> speaking (blue, playing the reply aloud) -> idle.
Backend TTS is edge-tts (ar-EG-ShakirNeural) — see zaki/tts.py.
"""

import io
import tempfile
import threading
import time
import wave
from pathlib import Path

import keyboard
import numpy as np
import pystray
import requests
import sounddevice as sd
from PIL import Image, ImageDraw
from playsound3 import playsound
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from autostart import is_autostart_enabled, set_autostart


class ClientSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    zaki_api_key: SecretStr = Field(validation_alias="ZAKI_API_KEY")
    backend_url: str = Field("http://127.0.0.1:8000", validation_alias="ZAKI_BACKEND_URL")
    hotkey: str = Field("ctrl+shift+z", validation_alias="ZAKI_HOTKEY")
    session_id: str = Field("windows-client", validation_alias="ZAKI_SESSION_ID")


settings = ClientSettings()

SAMPLE_RATE = 16000
CHANNELS = 1
SILENCE_RMS_THRESHOLD = 500  # int16 RMS amplitude below this counts as silence
SILENCE_DURATION = 1.2  # seconds of continuous silence to auto-stop recording
MAX_RECORD_SECONDS = 15.0  # hard cap so a stuck/noisy mic can't record forever

_STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "idle": (110, 110, 110),
    "listening": (220, 50, 50),
    "thinking": (230, 175, 30),
    "speaking": (60, 140, 220),
}


def _make_icon_image(color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=color)
    return img


class ZakiTrayApp:
    def __init__(self) -> None:
        self._state = "idle"
        self._icon = pystray.Icon(
            "zaki",
            _make_icon_image(_STATE_COLORS["idle"]),
            "Zaki — idle",
            menu=self._build_menu(),
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(f"Hotkey: {settings.hotkey}", None, enabled=False),
            pystray.MenuItem(
                "Start with Windows",
                self._toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.MenuItem("Quit", self._quit),
        )

    def _toggle_autostart(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        set_autostart(not is_autostart_enabled())

    def _quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        icon.stop()

    def _set_state(self, state: str) -> None:
        self._state = state
        self._icon.icon = _make_icon_image(_STATE_COLORS[state])
        self._icon.title = f"Zaki — {state}"

    def _record_until_silence(self) -> bytes:
        frames: list[np.ndarray] = []
        silence_start: float | None = None
        start = time.monotonic()

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16") as stream:
            while True:
                chunk, _ = stream.read(int(SAMPLE_RATE * 0.1))
                frames.append(chunk.copy())
                rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))

                if rms < SILENCE_RMS_THRESHOLD:
                    if silence_start is None:
                        silence_start = time.monotonic()
                    elif time.monotonic() - silence_start >= SILENCE_DURATION:
                        break
                else:
                    silence_start = None

                if time.monotonic() - start >= MAX_RECORD_SECONDS:
                    break

        audio = np.concatenate(frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes/sample
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    def _send_to_backend(self, wav_bytes: bytes) -> str:
        response = requests.post(
            f"{settings.backend_url}/api/assistant/audio",
            headers={"X-API-Key": settings.zaki_api_key.get_secret_value()},
            files={"audio": ("recording.wav", wav_bytes, "audio/wav")},
            data={"session_id": settings.session_id},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["reply"]

    def _fetch_speech(self, text: str) -> bytes:
        response = requests.post(
            f"{settings.backend_url}/api/tts",
            headers={"X-API-Key": settings.zaki_api_key.get_secret_value()},
            json={"text": text},
            timeout=30,
        )
        response.raise_for_status()
        return response.content

    def _play_audio(self, mp3_bytes: bytes) -> None:
        # playsound3 plays via Windows' native winmm/MCI API (ctypes, no
        # compiled extension) — pygame was tried first but has no prebuilt
        # wheel for this Python version and fails building from source
        # here. MCI needs a real file path, not an in-memory buffer, so
        # write to a temp file and clean it up once playback finishes.
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            temp_path = Path(f.name)
        try:
            playsound(str(temp_path))  # blocks until playback finishes (default block=True)
        finally:
            temp_path.unlink(missing_ok=True)

    def _on_hotkey(self) -> None:
        if self._state != "idle":
            return  # ignore re-trigger while already busy

        def worker() -> None:
            try:
                self._set_state("listening")
                wav_bytes = self._record_until_silence()

                self._set_state("thinking")
                reply = self._send_to_backend(wav_bytes)
                self._icon.notify(reply, "Zaki")  # text alongside audio — useful at a glance

                self._set_state("speaking")
                audio_bytes = self._fetch_speech(reply)
                self._play_audio(audio_bytes)
            except Exception as exc:  # a failed turn must not crash the tray app
                self._icon.notify(f"Error: {exc}", "Zaki")
            finally:
                self._set_state("idle")

        # keyboard's hotkey callback runs on its own hook thread; do the
        # actual recording/network/playback work off of it so the hook
        # stays responsive.
        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> None:
        keyboard.add_hotkey(settings.hotkey, self._on_hotkey)
        self._icon.run()  # blocks on the native message loop (main thread)


if __name__ == "__main__":
    ZakiTrayApp().run()
