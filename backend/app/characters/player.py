"""M5 (SPEC §104/R3): player character creation.

Reuses world resources (housing, jobs, economy) via the same primitives as
generate_population, with player-specific identity and control fields.
Deterministic id sequence: plr_0001...
"""

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import (
    Character,
    CharacterHealth,
    CharacterJob,
    CharacterNeeds,
    CharacterProfile,
    CharacterTrait,
    Job,
    Location,
    User,
)
from app.economy import mint, open_account
from app.events.events import EventType, log_event
from app.inventory import create_object


def next_player_id(session: Session, world_id: str) -> str:
    rows = (
        session.query(Character.id)
        .filter(Character.world_id == world_id, Character.id.like("plr_%"))
        .all()
    )
    max_n = 0
    for (cid,) in rows:
        try:
            max_n = max(max_n, int(cid.split("_")[1]))
        except (IndexError, ValueError):
            continue
    return f"plr_{max_n + 1:04d}"


def create_player_character(
    session: Session,
    settings: Settings,
    world_id: str,
    user: User,
    name: str,
    sex: str,
    age: int,
    game_timestamp: int,
    looks: str | None = None,
    biography: str | None = None,
) -> Character:
    """
    Create a player-owned character (R3).
    Raises ValueError with a user-facing reason on violation.
    """
    if age < 18:
        raise ValueError("player character must be 18+ (П4)")
    if sex not in ("M", "F"):
        raise ValueError("sex must be 'M' or 'F'")

    # Limit: one character per user (MVP)
    existing = (
        session.query(Character)
        .filter_by(world_id=world_id, user_id=user.id, alive=True)
        .first()
    )
    if existing is not None:
        raise ValueError("user already owns a living character")

    # Unique name in world
    if " " in name:
        first, last = name.split(" ", 1)
    else:
        first, last = name, ""
    if not first:
        raise ValueError("name must not be empty")
    duplicate = (
        session.query(Character)
        .filter(
            Character.world_id == world_id,
            Character.first_name == first,
            Character.last_name == last,
            Character.alive == True,  # noqa: E712
        )
        .first()
    )
    if duplicate is not None:
        raise ValueError("name already taken in this world")

    # Housing: next free house (one character per house, like NPC seeding)
    taken = {
        row[0]
        for row in session.query(Character.home_location_id)
        .filter(Character.world_id == world_id, Character.alive == True)  # noqa: E712
        .all()
        if row[0] is not None
    }
    house = (
        session.query(Location)
        .filter_by(world_id=world_id, type="house")
        .order_by(Location.id)
        .all()
    )
    free = [h for h in house if h.id not in taken]
    if not free:
        raise ValueError("no free housing available")

    # Job: round-robin after NPC count
    jobs = session.query(Job).order_by(Job.id).all()
    if not jobs:
        raise ValueError("no jobs available in the world. Seed world first.")
    npc_count = (
        session.query(Character)
        .filter(Character.world_id == world_id, Character.id.like("npc_%"))
        .count()
    )
    job = jobs[npc_count % len(jobs)]

    cid = next_player_id(session, world_id)
    character = Character(
        id=cid,
        world_id=world_id,
        type="player",
        user_id=user.id,
        control_mode="AUTONOMOUS",
        first_name=first,
        last_name=last,
        birth_date="2000-01-01",
        age=age,
        sex=sex,
        alive=True,
        location_id=free[0].id,
        home_location_id=free[0].id,
        occupation_id=job.id,
        looks=looks,
        biography=biography,
        created_at=game_timestamp,
        updated_at=game_timestamp,
    )
    session.add(character)
    session.flush()

    session.add(
        CharacterProfile(
            character_id=cid,
            biography=f"{first} {last} is a {age}-year-old {sex} working as a {job.title}.",
            birthplace=settings.population.birthplace,
            education="Standard",
            former_occupation="None",
            reason_for_arrival="Seeking a new start",
        )
    )
    session.add(
        CharacterNeeds(
            character_id=cid,
            hunger=100.0, thirst=100.0, energy=100.0, hygiene=100.0,
            comfort=100.0, social=100.0, safety=100.0,
            entertainment=100.0, privacy=100.0,
            updated_at=game_timestamp,
        )
    )
    session.add(CharacterHealth(character_id=cid, health=100.0, body_temperature=36.6, stress=0.0))
    session.add(CharacterJob(character_id=cid, job_id=job.id, started_at=game_timestamp))
    for trait_key in settings.population.trait_keys:
        session.add(CharacterTrait(character_id=cid, trait_key=trait_key, value=0))

    account = open_account(session, world_id, "character", cid)
    mint(
        session, world_id, game_timestamp, account.id,
        settings.economy.starting_balance, "Initial settlement funds"
    )
    for item_type in settings.generation.initial_items:
        create_object(
            session, world_id, item_type, free[0].id, 1,
            owner_character_id=cid
        )

    log_event(
        session, world_id, game_timestamp, EventType.CHARACTER_CREATED,
        actor_id=cid,
        location_id=free[0].id,
        payload={
            "name": f"{first} {last}", "age": age,
            "job": job.title, "type": "player", "user_id": user.id,
        },
    )
    session.flush()
    return character
