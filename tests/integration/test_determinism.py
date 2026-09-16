"""AE4 (spec §119): determinism — same seed, identical event sequence."""

from helpers import load_settings, run_full_simulation

from app.db.models import WorldEvent


def test_determinism_same_seed(tmp_path):
    settings = load_settings(tmp_path, "det1.db")
    _, session1, _ = run_full_simulation(settings, seed=42, days=5)
    events1 = (
        session1.query(
            WorldEvent.event_type, WorldEvent.actor_id, WorldEvent.game_timestamp
        ).order_by(WorldEvent.id).all()
    )

    settings2 = load_settings(tmp_path, "det2.db")
    _, session2, _ = run_full_simulation(settings2, seed=42, days=5)
    events2 = (
        session2.query(
            WorldEvent.event_type, WorldEvent.actor_id, WorldEvent.game_timestamp
        ).order_by(WorldEvent.id).all()
    )

    assert events1 == events2
    session1.close()
    session2.close()
