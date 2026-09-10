# Zaki backend (FastAPI). The Telegram long-polling loop and the
# reminders/daily-briefing scheduler both run as background asyncio tasks
# inside main.py's own lifespan — there's no separate process to manage
# here, so a single CMD is enough for the whole container.

FROM python:3.12-slim

# ffmpeg: required by telephony.py (Twilio Media Streams audio bridge —
# converts edge-tts's MP3 to the 8kHz PCM Twilio expects) and telegram.py
# (converts MP3 to the OGG/Opus format Telegram requires for a native
# voice-note reply). Without this, both features fail at runtime with a
# "ffmpeg: command not found" — verified locally that this exact gap is
# silent until something actually tries to synthesize voice.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY static ./static

RUN pip install --no-cache-dir -e .

# Render injects $PORT at runtime; default kept for `docker run` locally.
ENV PORT=8000
EXPOSE 8000

# Not baked into the image: ZAKI_TEMP_DIR / ZAKI_REMINDERS_DB_PATH default
# to relative paths (data/, handled by render.yaml's disk mount when
# present) — see config.py and render.yaml's comments on Render's free
# tier having no persistent disk at all.

CMD ["sh", "-c", "uvicorn zaki.main:app --host 0.0.0.0 --port ${PORT}"]
