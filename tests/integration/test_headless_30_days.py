"""AE2 (spec R10/§118): full 30-day / 20-NPC headless run."""

import pytest
from helpers import load_settings, run_full_simulation

pytestmark = [pytest.mark.slow]


def test_headless_30_days(tmp_path):
    settings = load_settings(tmp_path, "ae2_sim.db")
    report, session, _ = run_full_simulation(settings, seed=42, days=30)

    assert report["invariants_ok"] is True
    assert report["population_alive"] == 20

    events = report["events_by_type"]
    assert events.get("SALARY_PAID", 0) == 600  # 20 NPCs x 30 days
    assert events.get("SUPPLY_ARRIVED", 0) == 30  # 1 per day
    assert events.get("TASK_COMPLETED", 0) > 0
    assert events.get("CHARACTER_MOVED", 0) > 0
    session.close()
