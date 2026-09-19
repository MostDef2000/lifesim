import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.config import Settings
from app.db.models import OrganizationMember, Relationship, SchemaMeta, bootstrap
from app.events.events import EventType


def test_schema_constraints(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Test valid relationship
    rel = Relationship(
        world_id="test", character_a="char1", character_b="char2", affection=10.0
    )
    session.add(rel)
    session.commit()

    # Test invalid order: character_a >= character_b
    rel_inv = Relationship(
        world_id="test", character_a="char2", character_b="char1", affection=10.0
    )
    session.add(rel_inv)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

    # Test duplicate pair
    rel_dup = Relationship(
        world_id="test", character_a="char1", character_b="char2", affection=20.0
    )
    session.add(rel_dup)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

    # Test range constraint
    rel_range = Relationship(
        world_id="test", character_a="char1", character_b="char3", affection=150.0
    )
    session.add(rel_range)
    with pytest.raises(Exception):
        session.commit()
    session.rollback()

def test_schema_version(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()
    version = session.query(SchemaMeta).filter_by(key="version").first().value
    assert version == "0.9.0"

def _world_with_population(default_settings, rng_seed: int = 42,
                           social_enabled: bool = True):
    """bootstrap + seed_world + generate_population + seed_social."""
    import random

    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world
    from app.world.social_seed import seed_social

    default_settings.social.enabled = social_enabled
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, rng_seed)
    Session = sessionmaker(bind=engine)
    session = Session()
    world_id = default_settings.world.world_id
    seed_world(session, default_settings, world_id)
    rng = random.Random(rng_seed)
    generate_population(
        session, default_settings, rng, world_id,
        default_settings.world.initial_population
    )
    seed_social(session, default_settings, world_id, rng)
    return session, world_id

def test_org_seeding(default_settings):
    from app.db.models import Organization
    session, world_id = _world_with_population(default_settings)

    members = session.query(OrganizationMember).all()
    assert len(members) > 0

    # Every character is a member of the community org.
    from app.db.models import Character
    orgs = session.query(Organization).filter_by(world_id=world_id).all()
    community = next(o for o in orgs if o.type == "community")
    char_count = session.query(Character).filter_by(world_id=world_id).count()
    community_members = session.query(OrganizationMember).filter_by(
        organization_id=community.id
    ).count()
    assert community_members == char_count

    # Exactly one leader per org; leader is a member of that org.
    for org in orgs:
        org_leaders = session.query(OrganizationMember).filter_by(
            organization_id=org.id, role="leader"
        ).all()
        assert len(org_leaders) == 1
        assert org_leaders[0].character_id is not None
    # conftest world has a single job title and no leader_titles in org
    # config, so every org uses the lowest-id fallback leader.
    lowest = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .order_by(Character.id)
        .first()
    )
    for org in orgs:
        leader = session.query(OrganizationMember).filter_by(
            organization_id=org.id, role="leader"
        ).first()
        assert leader.character_id == lowest.id

def test_social_seed_disabled_is_noop(default_settings):
    """social.enabled=false: seed_social must not create rows or consume RNG
    (M1 byte-identity of worldgen state)."""
    from app.db.models import Character
    session, world_id = _world_with_population(
        default_settings, social_enabled=False
    )
    assert session.query(OrganizationMember).count() == 0
    assert session.query(Relationship).count() == 0
    assert session.query(Character).filter_by(world_id=world_id).count() == 20

def test_org_seeding_deterministic(default_settings):
    s1, _ = _world_with_population(default_settings, rng_seed=42)
    s2, _ = _world_with_population(default_settings, rng_seed=42)
    rows1 = [
        (m.organization_id, m.character_id, m.role)
        for m in s1.query(OrganizationMember).order_by(OrganizationMember.id).all()
    ]
    rows2 = [
        (m.organization_id, m.character_id, m.role)
        for m in s2.query(OrganizationMember).order_by(OrganizationMember.id).all()
    ]
    assert rows1 == rows2

def test_relationship_seeding(default_settings):
    session, _ = _world_with_population(default_settings)

    rels = session.query(Relationship).all()
    assert len(rels) == default_settings.social.initial_conflicts
    for r in rels:
        assert r.affection == -50.0
        assert r.character_a < r.character_b

def test_settings_validation():
    # Minimal dict as in conftest.py — no `social` key: defaults must apply.
    minimal_data = {
        "world": {"world_id": "test", "initial_population": 20,
                  "start_real_timestamp": "now"},
        "ticks": {"time_scale": 1.0,
                  "persistence_commit_interval_game_minutes": 60,
                  "snapshot_interval_game_days": 1},
        "needs": {
            "decay_rates": {"hunger": 0.1, "thirst": 0.1, "energy": 0.1,
                            "social": 0.1},
            "recovery_rates": {"SLEEP": {"energy": 1.0}, "EAT": {"hunger": 1.0},
                               "DRINK": {"thirst": 1.0}},
            "critical_thresholds": {"hunger": 20.0, "thirst": 20.0,
                                    "energy": 10.0},
            "health_decay_rate": 0.01,
        },
        "utility": {"weights": {"hunger": 1.0, "thirst": 1.0, "energy": 1.0,
                                "social": 1.0}},
        "actions": {
            "SLEEP": {"duration_minutes": 480, "max_duration_minutes": 480,
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
                     "base_utility_weight": 1.0},
        },
        "economy": {
            "starting_balance": 1000, "default_salary_day": 50,
            "shop_price_multiplier": 1.0,
            "prices": {"food_bread": 2}, "shop_stock": {"food_bread": 100},
            "kitchen_stock": {"food_bread": 20},
            "water_daily_supply_amount": 50.0,
            "water_initial_quantity": 1000.0,
            "salaries": {"farmer": 40},
            "organizations": {"community": {
                "name": "C", "type": "community", "location": "S",
                "starting_balance": 100}}
        },
        "movement": {"default_travel_minutes": 10,
                     "max_capacity_default": 100, "edges": []},
        "population": {"min_age": 18, "max_age": 80, "first_names": ["A"],
                       "last_names": ["B"], "trait_keys": ["S"], "jobs": []},
        "persistence": {"db_path": ":memory:", "wal_mode": True,
                        "synchronous": "NORMAL", "snapshot_dir": "s"},
        "invariants": {"max_duration_minutes": 1440, "min_balance": 0,
                       "health_range": [0, 100], "needs_range": [0, 100],
                       "max_death_rate_per_day": 0.1},
        "generation": {"trait_range": [-100, 100], "initial_items": []},
        "locations": {"houses_count": 24, "locations": {}},
    }
    settings = Settings(**minimal_data)
    assert settings.social.enabled is False

def test_event_type_count():
    # 13 (M2) + 6 (M3) + 3 (M4) = 24
    assert len(EventType) == 28
    assert "SOCIAL_INTERACTION" in EventType.__members__
    assert "RELATIONSHIP_CHANGED" in EventType.__members__
    assert "CONFLICT" in EventType.__members__
