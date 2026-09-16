"""AE1 config-sensitivity + AE3/AE4 spot checks."""

from pathlib import Path

import pytest
import yaml
from helpers import run_full_simulation

pytestmark = [pytest.mark.slow]


def test_ae1_config_sensitivity(tmp_path):
    """Same seed, 10x thirst decay/weight -> different event vector."""
    config_file = Path("config/default.yaml")
    with open(config_file, "r") as f:
        data = yaml.safe_load(f)
    data["persistence"]["db_path"] = str(tmp_path / "ae1_baseline.db")

    from app.config.config import Settings
    baseline_settings = Settings(**data)

    baseline_report, _, _ = run_full_simulation(
        baseline_settings, seed=42, days=30
    )
    baseline_events = baseline_report["events_by_type"]

    modified_settings = baseline_settings.model_copy(deep=True)
    modified_settings.needs.decay_rates.thirst *= 10.0
    modified_settings.utility.weights.thirst *= 10.0
    modified_settings.persistence.db_path = str(tmp_path / "ae1_modified.db")

    modified_report, _, _ = run_full_simulation(
        modified_settings, seed=42, days=30
    )
    modified_events = modified_report["events_by_type"]

    assert baseline_events != modified_events


def test_ae3_ae4_spot_checks(tmp_path):
    """Items are consumed and daily snapshots are written."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    config_file = Path("config/default.yaml")
    with open(config_file, "r") as f:
        data = yaml.safe_load(f)
    data["persistence"]["snapshot_dir"] = str(snap_dir)
    data["persistence"]["db_path"] = str(tmp_path / "spot_sim.db")

    from app.config.config import Settings
    settings = Settings(**data)

    report, session, _ = run_full_simulation(settings, seed=42, days=10)

    # AE3: item consumption
    assert report["events_by_type"].get("ITEM_CONSUMED", 0) > 0

    # AE4: snapshot files + DB rows
    snapshot_files = list(snap_dir.glob("*.db"))
    assert len(snapshot_files) > 0

    from app.db.models import WorldSnapshot
    assert session.query(WorldSnapshot).count() > 0
    session.close()
