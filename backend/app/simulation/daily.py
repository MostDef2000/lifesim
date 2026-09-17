from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterJob,
    Job,
    Location,
    Organization,
    WorldEvent,
)
from app.economy import open_account, transfer
from app.events.events import EventType, log_event
from app.inventory import add_supply


def run_daily_handlers(
    session: Session, world_id: str, game_day: int,
    game_timestamp: int, settings
):
    """
    Runs daily simulation handlers. Idempotent per (world_id, game_day).

    1. SALARY: Pay daily salary to each alive character with a job.
       - Amount = job.salary // 30 (integer division).
       - Transfer from employer organization account to character account.
       - If employer has insufficient funds, that payment is skipped.
       - Log SALARY_PAID event.

    2. SUPPLY: Add daily supplies to kitchen/shop and water to community.
       - Adds items from settings.economy.kitchen_stock/shop_stock.
       - Adds economy.water_daily_supply_amount water to the community org.
       - add_supply emits SUPPLY_ARRIVED (the per-day idempotency marker).
    """
    # Idempotency: a SUPPLY_ARRIVED event at this exact game_timestamp means
    # the day was processed (supply is the day marker).
    existing = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type=EventType.SUPPLY_ARRIVED.value,
        game_timestamp=game_timestamp
    ).first()
    if existing:
        return

    # --- 1. SALARY ---
    # Iterate ascending character id
    alive_chars = (
        session.query(Character)
        .filter_by(world_id=world_id, alive=True)
        .order_by(Character.id)
        .all()
    )

    for char in alive_chars:
        # Find job
        job_entry = session.query(CharacterJob).filter_by(character_id=char.id).first()
        if not job_entry:
            continue

        job = session.query(Job).filter_by(id=job_entry.job_id).one()
        employer = session.query(Organization).filter_by(id=job.organization_id).one()

        # Amount = salary // 30
        amount = job.salary // 30
        if amount <= 0:
            continue

        # Resolve accounts via the economy API (predictable M1 id format)
        from_acc = open_account(session, world_id, "organization", str(employer.id))
        to_acc = open_account(session, world_id, "character", char.id)

        try:
            # Attempt transfer. economy.transfer raises ValueError on insufficient funds.
            transfer(
                session=session,
                world_id=world_id,
                game_timestamp=game_timestamp,
                from_account_id=from_acc.id,
                to_account_id=to_acc.id,
                amount=amount,
                reason="SALARY",
            )

            # Log SALARY_PAID event
            log_event(
                session, world_id, game_timestamp, EventType.SALARY_PAID,
                actor_id=char.id,
                payload={"amount": amount, "day": game_day, "job_id": job.id}
            )
        except ValueError:
            # skip silently if employer balance < amount (deterministic; no event)
            pass

    # --- 2. SUPPLY ---
    # Community organization
    community_org = (
        session.query(Organization)
        .filter_by(world_id=world_id, type="community")
        .first()
    )

    # --- 3. M3 org policies (R3: BEFORE the early-return below so org
    # mechanics are not lost in degenerate worlds; gate R1 is inside) ---
    from app.policies.org import run_daily_org_policies
    run_daily_org_policies(session, world_id, settings, game_timestamp)

    # --- 4. M4 AI phase (worker drain + memory capture/consolidation;
    # gate R1 first line inside, zero work when llm disabled) ---
    from app.ai.worker import run_ai_phase
    run_ai_phase(session, world_id, settings, game_timestamp)

    if not community_org:
        return

    # Water supply (add_supply emits its own SUPPLY_ARRIVED event; the day
    # marker for idempotency is the event recorded at this game_timestamp).
    water_amount = settings.economy.water_daily_supply_amount
    add_supply(
        session, world_id, game_timestamp,
        resource_key="water",
        owner_type="organization",
        owner_id=str(community_org.id),
        amount=water_amount
    )

    # Food supplies (kitchen and shop stock as world_objects; add_supply in
    # inventory handles resource balances only).
    from app.inventory import create_object

    kitchen_loc = session.query(Location).filter_by(world_id=world_id, type="kitchen").first()
    shop_loc = session.query(Location).filter_by(world_id=world_id, type="shop").first()

    # Kitchen stock (community org owns the kitchen stock)
    if kitchen_loc:
        for item_type, qty in settings.economy.kitchen_stock.items():
            create_object(
                session, world_id, item_type, kitchen_loc.id, qty,
                owner_organization_id=community_org.id
            )

    # Shop stock
    if shop_loc:
        shop_org = (
            session.query(Organization)
            .filter_by(world_id=world_id, type="business")
            .first()
        )
        owner_id = shop_org.id if shop_org else community_org.id

        for item_type, qty in settings.economy.shop_stock.items():
            create_object(
                session, world_id, item_type, shop_loc.id, qty,
                owner_organization_id=owner_id
            )

    # NOTE: add_supply already emitted SUPPLY_ARRIVED at game_timestamp —
    # that event doubles as the idempotency day marker. No second event.

    session.flush()
