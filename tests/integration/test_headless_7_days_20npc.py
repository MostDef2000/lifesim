"""AE2 (spec §118): 7-day / 20-NPC headless run."""

from helpers import load_settings, run_full_simulation


def test_headless_7_days_20npc(tmp_path):
    settings = load_settings(tmp_path, "ae2_7day_sim.db")
    report, session, _ = run_full_simulation(settings, seed=42, days=7)

    assert report["invariants_ok"] is True
    assert report["population_alive"] == 20

    events = report["events_by_type"]
    assert events.get("SALARY_PAID", 0) == 140  # 20 NPCs x 7 days
    assert events.get("SUPPLY_ARRIVED", 0) == 7  # 1 per day
    assert events.get("TASK_COMPLETED", 0) > 0
    session.close()
