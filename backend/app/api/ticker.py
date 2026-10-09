"""#151 [alpha-v1][engine] Live tick driver: the API runtime advances game time.

Sibling of ws.py's event poll — but for TIME. A single asyncio task
(started in the FastAPI lifespan, so `vl1 serve` and `uvicorn app.run:app`
both get it) steps the simulation on a real-time interval by calling
Engine.step — batch-mode semantics reused verbatim (bulk needs decay,
progress_tick catch-up per app.actions.lifecycle, weather/fire/external/
crime/electricity phases, hour/day boundary phases). Nothing here
reinvents phase logic.

Speed: world_clock.time_scale means "game minutes advance time_scale×
faster than real" (see app.simulation.weather). The driver therefore earns
time_scale × live_tick_interval_s / 60 game-minutes per beat and steps
whole minutes once at least one has accumulated; the fractional remainder
is carried across beats, so long-run speed matches time_scale exactly.

Single writer per world (#151): the live driver REPLACES an external
cron-driven `vl1 simulate` — never run both against one DB. The batch
simulate CLI is a separate process that never builds the API app, so it
never starts this driver.

SQLite courtesy: one short-lived session per tick, committed at the end of
the step; no session is held across awaits. A failed tick (e.g. lock
contention) is logged and the loop continues — an error must not kill the
driver or the server. is_paused → the beat is skipped (task stays alive
and resumes when the world unpauses).
"""

import asyncio
import fcntl
import logging

logger = logging.getLogger("vl1.ticker")

# Process-lifetime fd holding the single-writer flock (None when this
# process is not the ticker — second worker/server on the same DB).
_TICK_LOCK_FD = None


def step_world_live(session_factory, settings, carry: float) -> float:
    """Run one live tick; return the fractional game-minute carry.

    Paused (or missing) clock → no-op. Fewer than 1 whole earned
    game-minute → no-op (the carry keeps accumulating). The clock row is
    re-read every tick, so /admin/world/pause, /resume and /timescale take
    effect without a restart.
    """
    from app.db.models import WorldClock as WorldClockModel
    from app.simulation.engine import Engine, TickScheduler, WorldClock

    world_id = settings.world.world_id
    with session_factory() as session:
        row = (
            session.query(WorldClockModel)
            .filter_by(world_id=world_id)
            .first()
        )
        if row is None or bool(row.is_paused):
            return carry  # paused: skip ticking, keep the task alive
        due = carry + float(row.time_scale) * (
            settings.world.live_tick_interval_s / 60.0
        )
        minutes = int(due)
        if minutes < 1:
            return due
        clock = WorldClock(initial_timestamp=int(row.game_timestamp))
        engine = Engine(clock, TickScheduler())
        # Exactly the batch-mode step (clock persist + weather/fire/external/
        # crime/police/electricity + needs → character → health → hour/day
        # boundaries); Engine.step writes the clock row back on this session.
        engine.step(
            minutes, session=session, world_id=world_id, settings=settings
        )
        session.commit()
        return due - minutes


async def run_tick_loop(settings, session_factory) -> None:
    """Tick, then sleep — forever. Never raises except on cancellation."""
    interval = settings.world.live_tick_interval_s
    carry = 0.0
    logger.info(
        "live tick driver started: world=%s interval_s=%s",
        settings.world.world_id, interval,
    )
    while True:
        try:
            carry = step_world_live(session_factory, settings, carry)
        except Exception:
            # #151: a failed tick (e.g. SQLite lock contention) must not
            # kill the driver or the server — log and continue on the next
            # beat. Note: the beat's earned fraction is NOT preserved here
            # (carry keeps its pre-beat value) — under sustained contention
            # the clock runs marginally slower than time_scale; acceptable
            # drift, never double-applied.
            logger.exception("live tick failed; continuing")
        await asyncio.sleep(interval)


def start_tick_driver(settings, session_factory) -> "asyncio.Task | None":
    """Spawn the driver on the running loop (lifespan startup).

    Single-writer guard (review F1): an OS-level flock on
    ``<db>.tick.lock`` — kernel-released on process death, so a crash can
    never wedge the next start. Only the process that takes the lock ticks;
    a second worker/server on the same DB logs and stays read-only on TIME.
    """
    db_path = settings.persistence.db_path
    lock_path = f"{db_path}.tick.lock"
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning(
            "live tick lock held by another process (%s); "
            "live ticking disabled in this one", lock_path,
        )
        fd.close()
        return None
    # Keep fd open for the process lifetime: closing it releases the lock.
    global _TICK_LOCK_FD
    _TICK_LOCK_FD = fd
    return asyncio.create_task(
        run_tick_loop(settings, session_factory), name="vl1-live-tick"
    )
