from app.simulation.engine import Engine, TickScheduler, WorldClock


def test_world_clock_progression():
    clock = WorldClock(initial_timestamp=0)
    clock.tick(1)
    assert clock.timestamp == 1
    clock.tick(59)
    assert clock.timestamp == 60

    state = clock.get_state()
    assert state.hour == 1
    assert state.day == 0

def test_world_clock_paused():
    clock = WorldClock(initial_timestamp=0, is_paused=True)
    clock.tick(10)
    assert clock.timestamp == 0
    clock.is_paused = False
    clock.tick(10)
    assert clock.timestamp == 10

def test_engine_phase_order():
    clock = WorldClock(0)
    scheduler = TickScheduler()
    engine = Engine(clock, scheduler)

    # Mock settings for engine.step
    class MockSettings:
        class ticks:
            persistence_commit_interval_game_minutes = 60
            snapshot_interval_game_days = 1

    # Step 1 minute
    engine.step(1, session=None, world_id="test", settings=MockSettings())
    # Expected phases: needs, character, health
    assert scheduler.history == ["needs", "character", "health"]

    # Step until hour boundary (60)
    clock.timestamp = 59
    engine.step(1, session=None, world_id="test", settings=MockSettings())
    # timestamp becomes 60 -> triggers economy
    assert "economy" in scheduler.history

def test_engine_day_boundary():
    clock = WorldClock(0)
    scheduler = TickScheduler()
    engine = Engine(clock, scheduler)

    # Mock settings for engine.step
    class MockSettings:
        class ticks:
            persistence_commit_interval_game_minutes = 60
            snapshot_interval_game_days = 1

    # Step to just before day end
    clock.timestamp = 1439
    engine.step(1, session=None, world_id="test", settings=MockSettings())
    # timestamp becomes 1440 -> triggers daily
    assert "daily" in scheduler.history
