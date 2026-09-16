import random

import pytest

from app.characters.generator import generate_population
from app.db.models import (
    Account,
    Character,
    CharacterJob,
    Job,
    Organization,
    WorldEvent,
)
from app.economy import get_balance
from app.simulation.daily import run_daily_handlers


def _first_char(session, world_id):
    return (
        session.query(Character)
        .filter_by(world_id=world_id)
        .order_by(Character.id)
        .first()
    )


def _char_balance(session, char):
    acc = session.query(Account).filter_by(
        owner_type="character", owner_id=char.id
    ).one()
    return acc, get_balance(session, acc.id)


def test_salary_paid_once_per_day(session, settings, world_id):
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    char = _first_char(session, world_id)
    acc, before = _char_balance(session, char)
    cj = session.query(CharacterJob).filter_by(character_id=char.id).one()
    job = session.query(Job).filter_by(id=cj.job_id).one()
    expected = job.salary // 30
    assert expected > 0

    run_daily_handlers(session, world_id, 1, 1440, settings)
    after_first = get_balance(session, acc.id)
    assert after_first == before + expected

    # Idempotent: second call for the same day must not pay again
    run_daily_handlers(session, world_id, 1, 1440, settings)
    assert get_balance(session, acc.id) == after_first

    events = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type="SALARY_PAID",
        game_timestamp=1440,
    ).all()
    assert len(events) == 20  # one per alive employed character


def test_salary_employer_balance_decreases(session, settings, world_id):
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    char = _first_char(session, world_id)
    cj = session.query(CharacterJob).filter_by(character_id=char.id).one()
    job = session.query(Job).filter_by(id=cj.job_id).one()
    employer = session.query(Organization).filter_by(id=job.organization_id).one()
    emp_acc = session.query(Account).filter_by(
        owner_type="organization", owner_id=str(employer.id)
    ).one()
    before = get_balance(session, emp_acc.id)

    run_daily_handlers(session, world_id, 1, 1440, settings)

    # 20 employed NPCs paid from this employer in the fixture (single job type)
    paid = 20 * (job.salary // 30)
    assert get_balance(session, emp_acc.id) == before - paid


def test_character_without_job_gets_nothing(session, settings, world_id):
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    char = _first_char(session, world_id)
    session.query(CharacterJob).filter_by(character_id=char.id).delete()
    session.flush()
    acc, before = _char_balance(session, char)

    run_daily_handlers(session, world_id, 1, 1440, settings)
    assert get_balance(session, acc.id) == before


def test_supply_adds_water_once(session, settings, world_id):
    from app.db.models import ResourceBalance

    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    community = session.query(Organization).filter_by(
        world_id=world_id, type="community"
    ).first()
    rb = session.query(ResourceBalance).filter_by(
        world_id=world_id,
        resource_key="water",
        owner_type="organization",
        owner_id=str(community.id),
    ).first()
    before = rb.quantity if rb else 0.0
    expected = settings.economy.water_daily_supply_amount

    run_daily_handlers(session, world_id, 1, 1440, settings)
    session.expire_all()
    rb = session.query(ResourceBalance).filter_by(
        world_id=world_id,
        resource_key="water",
        owner_type="organization",
        owner_id=str(community.id),
    ).one()
    assert rb.quantity == pytest.approx(before + expected)

    # Idempotent: rerun must not add water again
    run_daily_handlers(session, world_id, 1, 1440, settings)
    session.expire_all()
    rb = session.query(ResourceBalance).filter_by(
        world_id=world_id,
        resource_key="water",
        owner_type="organization",
        owner_id=str(community.id),
    ).one()
    assert rb.quantity == pytest.approx(before + expected)

    events = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type="SUPPLY_ARRIVED",
        game_timestamp=1440,
    ).all()
    assert len(events) == 1


def test_supply_adds_kitchen_and_shop_stock(session, settings, world_id):
    from app.db.models import Location, WorldObject

    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    run_daily_handlers(session, world_id, 1, 1440, settings)

    kitchen = session.query(Location).filter_by(
        world_id=world_id, type="kitchen"
    ).first()
    shop = session.query(Location).filter_by(world_id=world_id, type="shop").first()
    kitchen_food = (
        session.query(WorldObject)
        .filter(
            WorldObject.location_id == kitchen.id,
            WorldObject.object_type.like("food%"),
        )
        .count()
    )
    assert kitchen_food >= len(settings.economy.kitchen_stock)
    shop_stock = (
        session.query(WorldObject)
        .filter(
            WorldObject.location_id == shop.id,
            WorldObject.object_type.like("food%"),
        )
        .count()
    )
    assert shop_stock >= len(settings.economy.shop_stock)
