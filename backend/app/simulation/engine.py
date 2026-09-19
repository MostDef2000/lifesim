from dataclasses import dataclass
from typing import Callable, List


@dataclass
class WorldClockState:
    game_timestamp: int
    is_paused: bool
    hour: int
    day: int

class WorldClock:
    def __init__(self, initial_timestamp: int = 0, is_paused: bool = False):
        self.timestamp = initial_timestamp
        self.is_paused = is_paused

    def tick(self, minutes: int = 1):
        if not self.is_paused:
            self.timestamp += minutes
        return self.timestamp

    def get_state(self) -> WorldClockState:
        # 1440 mins per day, 60 per hour
        day = self.timestamp // 1440
        remaining = self.timestamp % 1440
        hour = remaining // 60
        return WorldClockState(
            game_timestamp=self.timestamp,
            is_paused=self.is_paused,
            hour=hour,
            day=day
        )

class TickScheduler:
    def __init__(self):
        self.phases: List[str] = ["needs", "character", "economy", "daily"]
        self.history: List[str] = []

    def run_phase(self, phase_name: str, callback: Callable):
        self.history.append(phase_name)
        callback()

class Engine:
    def __init__(self, clock: WorldClock, scheduler: TickScheduler):
        self.clock = clock
        self.scheduler = scheduler

    def step(self, minutes: int = 1, session=None, world_id=None, settings=None):
        # Bulk step: needs decay is linear over the window and the action
        # system's progress_tick uses catch-up semantics (bulk restore +
        # task-boundary processing), so one call per step is equivalent to
        # per-minute ticking while staying within the runtime budget.
        self.clock.tick(minutes)
        timestamp = self.clock.timestamp


        # Persist the clock so reports and snapshots see the real time
        if session is not None and world_id is not None:
            from app.db.models import WorldClock as WorldClockModel
            db_clock = (
                session.query(WorldClockModel)
                .filter_by(world_id=world_id)
                .first()
            )
            if db_clock is not None:
                db_clock.game_timestamp = timestamp

        # 0. Weather ensure (010 §71): one row per crossed day
        if session is not None and world_id is not None and settings is not None:
            from app.simulation.weather import get_or_create_weather

            day = timestamp // 1440
            self.scheduler.run_phase(
                "weather",
                lambda: get_or_create_weather(session, world_id, day, settings)
            )
            # 011 (§72): daily fire phase (spread/damage/burnout/spontaneous)
            from app.simulation.fire import run_fire_phase

            self.scheduler.run_phase(
                "fire",
                lambda: run_fire_phase(session, world_id, day, settings)
            )
            # 013: NPC utility trips + supply/demand multipliers (flag-gated)
            from app.external.npc_trips import (
                run_npc_trip_phase,
                update_service_multipliers,
            )

            self.scheduler.run_phase(
                "external_followups",
                lambda: (
                    update_service_multipliers(session, world_id, day, settings),
                    run_npc_trip_phase(session, world_id, day, settings),
                )
            )
            # 016 (§32-35): crime commit/witness + police resolution
            from app.crime.crime import run_crime_phase, run_police_phase

            self.scheduler.run_phase(
                "crime",
                lambda: run_crime_phase(
                    session, world_id, day, timestamp, settings)
            )
            self.scheduler.run_phase(
                "police",
                lambda: run_police_phase(
                    session, world_id, day, timestamp, settings)
            )
            # 017 (§26): house electricity supply/demand (flag-gated)
            from app.simulation.electricity import run_electricity_phase

            self.scheduler.run_phase(
                "electricity",
                lambda: run_electricity_phase(
                    session, world_id, day, timestamp, settings)
            )

        # 1. Needs tick: apply decay for the whole window
        self.scheduler.run_phase(
            "needs",
            lambda: self._phase_needs(session, world_id, timestamp, minutes, settings)
        )

        # 2. Character tick: progress tasks (catch-up to `timestamp`)
        self.scheduler.run_phase(
            "character",
            lambda: self._phase_character(session, world_id, timestamp, settings)
        )

        # 2.5 Health decay (R4): critical thresholds drain health; health
        # <= 0 -> death path. Evaluated ONCE per step on the END-of-step
        # needs state (after catch-up restores) — a documented M1 bulk
        # checkpoint: same-day recoveries (eat/sleep before day end) do not
        # drain health, sustained multi-step starvation does.
        self.scheduler.run_phase(
            "health",
            lambda: self._phase_health(session, world_id, timestamp, minutes, settings)
        )

        # 3. Boundary detection
        # Hour rollover
        if timestamp % 60 == 0:
            self.scheduler.run_phase(
                "economy",
                lambda: self._phase_economy(session, world_id, timestamp)
            )

        # Day rollover
        if timestamp % 1440 == 0:
            day = timestamp // 1440
            self.scheduler.run_phase(
                "daily",
                lambda: self._phase_daily(session, world_id, day, timestamp, settings)
            )

        # Persistence commit interval
        if (
            settings and session
            and timestamp % settings.ticks.persistence_commit_interval_game_minutes == 0
        ):
            session.commit()

    def _phase_needs(self, session, world_id, timestamp, minutes, settings):
        if session is None:
            return
        from app.characters.needs import apply_needs_decay
        apply_needs_decay(session, world_id, timestamp, minutes, settings)

    def _phase_character(self, session, world_id, timestamp, settings):
        if session is None:
            return
        # Logic for character tasks (M1 already has lifecycle.progress_tick)
        from app.actions.lifecycle import progress_tick
        progress_tick(session, world_id, timestamp, settings)

    def _phase_health(self, session, world_id, timestamp, minutes, settings):
        if session is None:
            return
        from app.characters.health import apply_health_decay
        apply_health_decay(session, world_id, timestamp, minutes, settings)

    def _phase_economy(self, session, world_id, timestamp):
        # M2: hourly economy tick reserved (no-op in M1).
        pass

    def _phase_daily(self, session, world_id, day, timestamp, settings):
        if session is None:
            return
        from app.simulation.daily import run_daily_handlers
        from app.simulation.snapshots import take_snapshot

        # Run daily handlers (salary, supply)
        run_daily_handlers(session, world_id, day, timestamp, settings)

        # Take snapshot if it's the interval
        if settings and day % settings.ticks.snapshot_interval_game_days == 0:
            # Path is derived from settings or a default runs/ directory
            snapshot_dir = (
                settings.persistence.snapshot_dir
                if settings.persistence else f"runs/{world_id}/snapshots"
            )
            # We pass the engine from the session (simplified)
            take_snapshot(session.get_bind(), session, world_id, timestamp, snapshot_dir)

