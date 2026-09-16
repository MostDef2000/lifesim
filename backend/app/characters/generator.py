import random

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterHealth,
    CharacterJob,
    CharacterNeeds,
    CharacterProfile,
    CharacterTrait,
    Job,
    Location,
)
from app.economy import mint, open_account
from app.events.events import EventType, log_event
from app.inventory import create_object


def generate_population(session: Session, settings, rng: random.Random, world_id: str, count: int):
    """
    Deterministic NPC creation.
    - unique first+last names from config lists
    - sex via rng
    - age via rng in [min_age..max_age], validated >= 18
    - traits: one row per config trait_key, value rng [-100, 100]
    - profile: templated biography (deterministic)
    - housing: 1 NPC per house, ascending location id
    - jobs: round-robin by ascending job id
    - money: open_account + mint(starting_balance)
    - inventory: generation.initial_items at home location
    - needs: all 100.0; health: 100.0
    - event: CHARACTER_CREATED per NPC
    - ids: npc_0001...npc_00NN
    """
    # Get houses (type='house', sorted by id)
    houses = (
        session.query(Location)
        .filter_by(world_id=world_id, type="house")
        .order_by(Location.id)
        .all()
    )
    if len(houses) < count:
        raise ValueError(f"Not enough houses for {count} NPCs. Only {len(houses)} available.")

    # Get jobs (sorted by id)
    jobs = session.query(Job).order_by(Job.id).all()
    if not jobs:
        raise ValueError("No jobs available in the world. Seed world first.")

    # Pre-process name lists to ensure uniqueness if possible
    first_names = settings.population.first_names
    last_names = settings.population.last_names

    shuffled_first = list(first_names)
    shuffled_last = list(last_names)
    rng.shuffle(shuffled_first)
    rng.shuffle(shuffled_last)

    for i in range(1, count + 1):
        npc_id = f"npc_{i:04d}"

        # Names: distinct (first, last) pairs drawn from the cross-product
        # so full names are unique for count <= len(first) * len(last).
        first = shuffled_first[i % len(shuffled_first)]
        last = shuffled_last[(i // len(shuffled_first)) % len(shuffled_last)]

        # Sex
        sex = rng.choice(["M", "F"])

        # Age
        min_age = settings.population.min_age
        max_age = settings.population.max_age
        age = rng.randint(min_age, max_age)
        if age < 18:
            raise ValueError(f"Generated NPC {npc_id} age {age} is below 18")

        # Housing (ascending id)
        house = houses[i-1]

        # Job (round-robin)
        job = jobs[(i-1) % len(jobs)]

        # Deterministic Profile
        bio = f"{first} {last} is a {age}-year-old {sex} working as a {job.title}."
        birthplace = settings.population.birthplace
        education = "Standard"
        former_occupation = "None"
        reason_for_arrival = "Seeking a new start"

        # Create Character
        npc = Character(
            id=npc_id,
            world_id=world_id,
            first_name=first,
            last_name=last,
            birth_date="2000-01-01",
            age=age,
            sex=sex,
            alive=True,
            location_id=house.id,
            home_location_id=house.id,
            occupation_id=job.id,
            created_at=0,
            updated_at=0
        )
        session.add(npc)
        session.flush()

        # Character Profile
        profile = CharacterProfile(
            character_id=npc_id,
            biography=bio,
            birthplace=birthplace,
            education=education,
            former_occupation=former_occupation,
            reason_for_arrival=reason_for_arrival
        )
        session.add(profile)

        # Traits
        for trait_key in settings.population.trait_keys:
            trait = CharacterTrait(
                character_id=npc_id,
                trait_key=trait_key,
                value=rng.randint(-100, 100)
            )
            session.add(trait)

        # Needs
        needs = CharacterNeeds(
            character_id=npc_id,
            hunger=100.0,
            thirst=100.0,
            energy=100.0,
            hygiene=100.0,
            comfort=100.0,
            social=100.0,
            safety=100.0,
            entertainment=100.0,
            privacy=100.0,
            updated_at=0
        )
        session.add(needs)

        # Health
        health = CharacterHealth(
            character_id=npc_id,
            health=100.0,
            body_temperature=36.6,
            stress=0.0
        )
        session.add(health)

        # Job entry
        c_job = CharacterJob(
            character_id=npc_id,
            job_id=job.id,
            started_at=0
        )
        session.add(c_job)

        # Economy: Account and Mint
        account = open_account(session, world_id, "character", npc_id)
        mint(
            session, world_id, 0, account.id,
            settings.economy.starting_balance, "Initial settlement funds"
        )

        # Inventory: Initial Items
        for item_type in settings.generation.initial_items:
            create_object(
                session, world_id, item_type, house.id, 1,
                owner_character_id=npc_id
            )

        # Event
        log_event(
            session, world_id, 0, EventType.CHARACTER_CREATED,
            actor_id=npc_id,
            payload={"name": f"{first} {last}", "age": age, "job": job.title}
        )

        session.flush()
