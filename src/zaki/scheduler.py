"""APScheduler wiring for reminders and the daily briefing.

Persistent job store — SQLAlchemyJobStore, not the in-memory default. On
startup APScheduler reloads every stored job and reschedules it, so a
reminder set via Telegram survives a backend restart or a full reboot.
Two backends, chosen by whether ZAKI_REMINDERS_DB_URL is set:

- SQLite (local dev default, ZAKI_REMINDERS_DB_PATH) — zero extra
  infrastructure, but the file has to live on a disk that's actually still
  there next time the process starts.
- Postgres (ZAKI_REMINDERS_DB_URL, e.g. the same Supabase project the rest
  of this app already uses) — the only option that actually survives on
  Render's free tier: free web services there have NO persistent disk at
  all (that's a paid add-on) and the filesystem resets on every redeploy
  and every spin-down/wake cycle, so a SQLite file would silently lose
  every reminder on the very first restart. Postgres isolates its tables
  into their own schema (zaki_scheduler), same reasoning as Evolution
  API's separate "evolution" schema elsewhere in this project — shares the
  database, never the tables, with Zaki's own conversation_log/tasks/notes.

Job functions take only plain, trivially-picklable arguments (chat_id:
int, message: str) rather than live objects like Settings/ContextProvider.
Anything passed to add_job's kwargs gets pickled by the job store —
storing a live settings/context snapshot would be fragile across restarts
and pointless besides, since get_settings()/get_context_provider() are
cheap to call fresh inside the job itself.

Delivery always goes through Telegram (text, then a best-effort edge-tts
voice note) — it's the only channel in this project with genuine push
capability; the web frontend can only be polled by its own user, never
pushed to from a background job.
"""

import logging
import os
from datetime import datetime

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# A reminder about a specific thing is still worth delivering even if the
# laptop was off/asleep well past the trigger time — no cutoff.
_REMINDER_MISFIRE_GRACE: int | None = None
# The daily briefing shouldn't fire hours late and still call itself
# "morning" — bounded grace instead of unlimited.
_BRIEFING_MISFIRE_GRACE = 6 * 3600

_SCHEDULER_SCHEMA = "zaki_scheduler"


def configure_scheduler(*, db_url: str | None, db_path: str) -> None:
    """Wires up the persistent job store. Must be called once, before
    start_scheduler() — APScheduler loads every stored job as part of
    scheduler.start().
    """
    if db_url:
        connect_args = {}
        if db_url.startswith("postgresql"):
            # Note the space after -c: psycopg2/libpq's options string is a
            # literal GUC-assignment command ("-c name=value"); "-cname=..."
            # with no space is silently ignored, and the table lands in
            # whatever the connection's default search_path was instead
            # (caught by actually checking where the table landed, not by
            # assuming this worked).
            connect_args = {"options": f"-c search_path={_SCHEDULER_SCHEMA}"}
        engine = create_engine(db_url, connect_args=connect_args)
        if db_url.startswith("postgresql"):
            with engine.begin() as conn:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEDULER_SCHEMA}"))
    else:
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}")

    scheduler.add_jobstore(SQLAlchemyJobStore(engine=engine), alias="default")


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


async def _deliver_text_and_voice(chat_id: int, message: str) -> None:
    # Deferred: zaki.telegram is fine to import at module level here too
    # (no cycle either way), but kept deferred for the same reason as
    # elsewhere in this codebase — this module gets imported very early
    # (tools/reminders.py, main.py's lifespan) and deferring keeps every
    # cross-module import lazy and consistent rather than mixing styles.
    from zaki import telegram as telegram_module

    try:
        await telegram_module.send_telegram_message(chat_id, message)
    except Exception:
        logger.exception("Failed to deliver text to Telegram chat_id=%s", chat_id)
        return  # the voice note is a nice-to-have; the text delivery is the one that matters

    try:
        await telegram_module.send_telegram_voice(chat_id, message)
    except Exception:
        logger.exception("Failed to synthesize/send voice note for chat_id=%s", chat_id)


def schedule_reminder(*, chat_id: int, remind_at: datetime, message: str) -> str:
    job = scheduler.add_job(
        _deliver_text_and_voice,
        "date",
        run_date=remind_at,
        kwargs={"chat_id": chat_id, "message": message},
        misfire_grace_time=_REMINDER_MISFIRE_GRACE,
    )
    return job.id


async def _deliver_daily_briefing(chat_id: int) -> None:
    # Deferred for the same circular-import reason as telephony.py/
    # telegram.py — zaki.pipeline pulls in zaki.tools via zaki.providers.base.
    from zaki.config import get_settings
    from zaki.context import get_context_provider
    from zaki.pipeline import run_assistant_pipeline

    settings = get_settings()
    context = get_context_provider()

    try:
        result = await run_assistant_pipeline(
            text=(
                "الآن موعد التحية الصباحية اليومية. قدّم تحية صباحية موجزة، "
                "ثم استخدم أدواتك المتاحة لتلخيص مهامّي المعلّقة ومواعيدي "
                "القادمة اليوم إن وُجدت."
            ),
            session_id=f"telegram:{chat_id}:daily-briefing",
            settings=settings,
            context=context,
        )
    except RuntimeError:
        logger.exception("Daily briefing generation failed for chat_id=%s", chat_id)
        return

    await _deliver_text_and_voice(chat_id, result.reply)


def schedule_daily_briefing(*, chat_id: int, hour: int, minute: int) -> None:
    scheduler.add_job(
        _deliver_daily_briefing,
        CronTrigger(hour=hour, minute=minute),
        kwargs={"chat_id": chat_id},
        id="daily_briefing",
        replace_existing=True,
        misfire_grace_time=_BRIEFING_MISFIRE_GRACE,
    )


def _cleanup_stray_temp_files(temp_dir: str, max_age_hours: float) -> None:
    """Everything the TTS/STT/ffmpeg pipeline itself touches is in-memory
    (BytesIO/subprocess pipes, never a disk file) by design — this exists
    purely as a defensive net for whatever else might land in the temp
    dir (e.g. Starlette's UploadFile spilling a large upload to disk) and
    then get orphaned by a crash before its own cleanup ran.
    """
    if not os.path.isdir(temp_dir):
        return
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for name in os.listdir(temp_dir):
        path = os.path.join(temp_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.info("Removed stray temp file: %s", path)
        except OSError:
            logger.exception("Failed to remove stray temp file: %s", path)


def schedule_temp_cleanup(*, temp_dir: str, max_age_hours: float = 1.0) -> None:
    scheduler.add_job(
        _cleanup_stray_temp_files,
        IntervalTrigger(minutes=30),
        kwargs={"temp_dir": temp_dir, "max_age_hours": max_age_hours},
        id="temp_cleanup",
        replace_existing=True,
    )
