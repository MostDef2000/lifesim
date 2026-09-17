import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.config import Settings
from app.db.models import OrgLaw, OrgLawViolation, bootstrap
from app.events.events import EventType
from app.policies.org import choose_law, enact_initial_laws


def test_org_law_schema(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Test unique constraint on OrgLaw (world_id, organization_id)
    law1 = OrgLaw(
        world_id="w1", organization_id=1, law_key="law1",
        enacted_by_character_id="c1", enacted_day=0, enacted_at=0,
    )
    session.add(law1)
    session.commit()

    law2 = OrgLaw(
        world_id="w1", organization_id=1, law_key="law2",
        enacted_by_character_id="c2", enacted_day=1, enacted_at=100,
    )
    session.add(law2)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

def test_org_law_violation_schema(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Test unique constraint (world_id, organization_id, law_key, character_id, game_day)
    v1 = OrgLawViolation(
        world_id="w1", organization_id=1, law_key="law1", character_id="c1",
        game_day=1, game_timestamp=100, fine_paid=0,
    )
    session.add(v1)
    session.commit()

    v2 = OrgLawViolation(
        world_id="w1", organization_id=1, law_key="law1", character_id="c1",
        game_day=1, game_timestamp=200, fine_paid=5,
    )
    session.add(v2)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

def test_settings_org_defaults():
    # Minimal dict as in conftest.py — no `org` key: defaults must apply.
    settings = Settings(
        world={"world_id": "test", "initial_population": 20, "start_real_timestamp": "now"},
        ticks={"time_scale": 1.0, "persistence_commit_interval_game_minutes": 60,
               "snapshot_interval_game_days": 1},
        needs={"decay_rates": {"hunger": 0.1, "thirst": 0.1, "energy": 0.1, "social": 0.1},
               "recovery_rates": {"SLEEP": {"energy": 1.0}, "EAT": {"hunger": 1.0},
                                  "DRINK": {"thirst": 1.0}},
               "critical_thresholds": {"hunger": 20.0, "thirst": 20.0, "energy": 10.0},
               "health_decay_rate": 0.01},
        utility={"weights": {"hunger": 1.0, "thirst": 1.0, "energy": 1.0, "social": 1.0}},
        actions={"SLEEP": {"duration_minutes": 480, "max_duration_minutes": 480,
                           "base_utility_weight": 1.0},
                 "EAT": {"duration_minutes": 15, "max_duration_minutes": 30,
                         "base_utility_weight": 1.0},
                 "DRINK": {"duration_minutes": 5, "max_duration_minutes": 15,
                           "base_utility_weight": 1.0},
                 "WORK": {"duration_minutes": 480, "max_duration_minutes": 600,
                          "base_utility_weight": 1.0},
                 "MOVE": {"duration_minutes": 10, "max_duration_minutes": 120,
                          "base_utility_weight": 1.0},
                 "BUY_ITEM": {"duration_minutes": 5, "max_duration_minutes": 30,
                              "base_utility_weight": 1.0},
                 "IDLE": {"duration_minutes": 1, "max_duration_minutes": 60,
                          "base_utility_weight": 1.0}},
        economy={"starting_balance": 1000, "default_salary_day": 50,
                 "shop_price_multiplier": 1.0, "prices": {"food_bread": 2},
                 "shop_stock": {"food_bread": 100}, "kitchen_stock": {"food_bread": 20},
                 "water_daily_supply_amount": 50.0, "water_initial_quantity": 1000.0,
                 "salaries": {"farmer": 40},
                 "organizations": {"community": {"name": "C", "type": "community",
                                                 "location": "S", "starting_balance": 100}}},
        movement={"default_travel_minutes": 10, "max_capacity_default": 100, "edges": []},
        population={"min_age": 18, "max_age": 80, "first_names": ["A"], "last_names": ["B"],
                    "trait_keys": ["S"], "jobs": []},
        persistence={"db_path": ":memory:", "wal_mode": True, "synchronous": "NORMAL",
                     "snapshot_dir": "s"},
        invariants={"max_duration_minutes": 1440, "min_balance": 0, "health_range": [0, 100],
                    "needs_range": [0, 100], "max_death_rate_per_day": 0.1},
        generation={"trait_range": [-100, 100], "initial_items": []},
        locations={"houses_count": 24, "locations": {}},
    )
    assert settings.org.enabled is False
    assert settings.org.election_interval_days == 7
    assert settings.org.dues_per_day == 1
    assert settings.org.feast_interval_days == 14
    assert settings.org.feast_cost == 30
    assert settings.org.feast_social_boost == 20.0
    assert settings.org.reconciliation.enabled is True
    assert settings.org.reconciliation.target_affection == -30.0
    assert settings.org.laws.enabled is True
    assert settings.org.laws.catalog["no_conflict"].fine == 5
    assert settings.org.laws.catalog["night_home"].fine == 2

def test_choose_law():
    catalog = {
        "law_a": {"enact_traits": {"discipline": 1, "sociability": 1,
                                   "risk_tolerance": 1}},
        "law_b": {"enact_traits": {"discipline": 1, "sociability": -1,
                                   "risk_tolerance": 1}},
    }
    # Maximize dot product
    assert choose_law({"discipline": 10, "sociability": 10,
                       "risk_tolerance": 10}, catalog) == "law_a"
    assert choose_law({"discipline": 10, "sociability": -10,
                       "risk_tolerance": 10}, catalog) == "law_b"
    # Tie -> First in order
    assert choose_law({"discipline": 10, "sociability": 0,
                       "risk_tolerance": 10}, catalog) == "law_a"
    # Missing traits -> 0
    assert choose_law({}, catalog) == "law_a"
    # Pydantic LawEntry catalog (dual-path with dicts)
    from app.config.config import LawEntry
    pyd_catalog = {
        "law_a": LawEntry(fine=1, enact_traits={"discipline": 1, "sociability": 1,
                                                "risk_tolerance": 1}),
        "law_b": LawEntry(fine=2, enact_traits={"discipline": 1, "sociability": -1,
                                                "risk_tolerance": 1}),
    }
    assert choose_law({"discipline": 10, "sociability": 10,
                       "risk_tolerance": 10}, pyd_catalog) == "law_a"
    assert choose_law({"discipline": 10, "sociability": -10,
                       "risk_tolerance": 10}, pyd_catalog) == "law_b"

def test_enact_initial_laws_hook(default_settings):
    import random

    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world
    from app.world.social_seed import seed_social

    # Case 1: Org Enabled (gate R1 requires org AND social)
    default_settings.org.enabled = True
    default_settings.social.enabled = True
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()
    world_id = default_settings.world.world_id
    seed_world(session, default_settings, world_id)
    rng = random.Random(42)
    generate_population(session, default_settings, rng, world_id, 20)
    seed_social(session, default_settings, world_id, rng)

    enact_initial_laws(session, world_id, default_settings, timestamp=100)
    session.commit() # Ensure changes are committed for the assertion

    assert session.query(OrgLaw).count() == 1
    law = session.query(OrgLaw).first()
    assert law.enacted_day == 0
    assert law.enacted_at == 100

    # Check event + payload (spec R5)
    from app.db.models import WorldEvent
    event = session.query(WorldEvent).filter_by(event_type=EventType.LAW_ENACTED.value).first()
    assert event is not None
    assert event.game_timestamp == 100
    assert event.actor_id == law.enacted_by_character_id
    assert event.target_id is None
    import json
    payload = json.loads(event.payload)
    assert payload["organization_id"] == law.organization_id
    assert payload["law_key"] == law.law_key
    assert payload["replaced_law_key"] is None

    # Idempotency: second call must not add rows or events
    enact_initial_laws(session, world_id, default_settings, timestamp=200)
    session.commit()
    assert session.query(OrgLaw).count() == 1
    assert session.query(WorldEvent).filter_by(
        event_type=EventType.LAW_ENACTED.value).count() == 1

    # Case 2: Org Disabled
    default_settings.org.enabled = False
    default_settings.social.enabled = False
    engine_off = create_engine("sqlite:///:memory:")
    bootstrap(engine_off, default_settings, 42)
    Session_off = sessionmaker(bind=engine_off)
    session_off = Session_off()
    world_id_off = default_settings.world.world_id
    seed_world(session_off, default_settings, world_id_off)
    rng_off = random.Random(42)
    generate_population(session_off, default_settings, rng_off, world_id_off, 20)
    seed_social(session_off, default_settings, world_id_off, rng_off)

    enact_initial_laws(session_off, world_id_off, default_settings, timestamp=100)
    session_off.commit()
    assert session_off.query(OrgLaw).count() == 0

    from app.db.models import WorldEvent
    assert session_off.query(WorldEvent).filter_by(
        event_type=EventType.LAW_ENACTED.value).count() == 0

    # Case 3: org enabled, social disabled — gate R1 must block (byte-identity)
    default_settings.org.enabled = True
    default_settings.social.enabled = False
    engine_gated = create_engine("sqlite:///:memory:")
    bootstrap(engine_gated, default_settings, 42)
    Session_gated = sessionmaker(bind=engine_gated)
    session_gated = Session_gated()
    world_id_gated = default_settings.world.world_id
    seed_world(session_gated, default_settings, world_id_gated)
    rng_gated = random.Random(42)
    generate_population(session_gated, default_settings, rng_gated, world_id_gated, 20)
    seed_social(session_gated, default_settings, world_id_gated, rng_gated)

    enact_initial_laws(session_gated, world_id_gated, default_settings, timestamp=100)
    session_gated.commit()
    assert session_gated.query(OrgLaw).count() == 0
    assert session_gated.query(WorldEvent).filter_by(
        event_type=EventType.LAW_ENACTED.value).count() == 0

def test_cli_org_flag(default_settings):
    # Mirror how --social is testable by checking the model_copy logic
    # Use the default_settings fixture to avoid Pydantic validation errors
    s = default_settings.model_copy()

    args_org = True
    s = s.model_copy(update={"org": s.org.model_copy(update={"enabled": args_org})})
    assert s.org.enabled is True
