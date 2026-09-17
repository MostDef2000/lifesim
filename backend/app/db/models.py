from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import Session, declarative_base

from ..config.config import Settings

Base = declarative_base()


# 1. worlds
class World(Base):
    __tablename__ = "worlds"
    id = Column(String, primary_key=True)
    seed = Column(Integer, nullable=False)
    start_real = Column(Text, nullable=False)
    config_sha256 = Column(Text, nullable=False)

# 2. world_clock
class WorldClock(Base):
    __tablename__ = "world_clock"
    world_id = Column(String, ForeignKey("worlds.id"), primary_key=True)
    game_timestamp = Column(Integer, default=0, nullable=False)
    time_scale = Column(Float, nullable=False)
    is_paused = Column(Boolean, default=False, nullable=False)
    last_real_timestamp = Column(Text, nullable=False)

# 3. schema_meta
class SchemaMeta(Base):
    __tablename__ = "schema_meta"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)

# 4. locations
class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    z = Column(Float, nullable=True)
    capacity = Column(Integer, nullable=True)
    access_level = Column(String, default="public", nullable=False)

# 5. location_links
class LocationLink(Base):
    __tablename__ = "location_links"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    from_location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    to_location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    travel_minutes = Column(Integer, nullable=False)

# 6. characters
class Character(Base):
    __tablename__ = "characters"
    id = Column(String, primary_key=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    account_id = Column(String, nullable=True)
    type = Column(String, default="npc", nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    birth_date = Column(Text, nullable=False)
    age = Column(Integer, nullable=False)
    sex = Column(String, nullable=False)
    gender_identity = Column(String, nullable=True)
    alive = Column(Boolean, default=True, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    home_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    occupation_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)
    death_game_timestamp = Column(Integer, nullable=True)
    death_cause = Column(Text, nullable=True)
    # M5 (SPEC §60/104): player ownership + control mode
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    control_mode = Column(String, nullable=False, default="AUTONOMOUS")  # AUTONOMOUS|GUIDED|DIRECT

# 7. character_profiles
class CharacterProfile(Base):
    __tablename__ = "character_profiles"
    character_id = Column(String, ForeignKey("characters.id"), primary_key=True)
    biography = Column(Text, nullable=True)
    birthplace = Column(Text, nullable=True)
    education = Column(Text, nullable=True)
    former_occupation = Column(Text, nullable=True)
    reason_for_arrival = Column(Text, nullable=True)
    religion_or_worldview = Column(Text, nullable=True)
    life_goals = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

# 8. character_traits
class CharacterTrait(Base):
    __tablename__ = "character_traits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    trait_key = Column(String, nullable=False)
    value = Column(Integer, nullable=False)

# 9. character_needs
class CharacterNeeds(Base):
    __tablename__ = "character_needs"
    character_id = Column(String, ForeignKey("characters.id"), primary_key=True)
    hunger = Column(Float, nullable=False)
    thirst = Column(Float, nullable=False)
    energy = Column(Float, nullable=False)
    hygiene = Column(Float, nullable=False)
    comfort = Column(Float, nullable=False)
    social = Column(Float, nullable=False)
    safety = Column(Float, nullable=False)
    entertainment = Column(Float, nullable=False)
    privacy = Column(Float, nullable=False)
    updated_at = Column(Integer, nullable=False)

# 10. character_health
class CharacterHealth(Base):
    __tablename__ = "character_health"
    character_id = Column(String, ForeignKey("characters.id"), primary_key=True)
    health = Column(Float, nullable=False)
    body_temperature = Column(Float, nullable=False)
    stress = Column(Float, nullable=False)
    pain = Column(Float, default=0.0, nullable=False)
    blood_loss = Column(Float, default=0.0, nullable=False)
    intoxication = Column(Float, default=0.0, nullable=False)

# 11. organizations
class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    leader_character_id = Column(String, ForeignKey("characters.id"), nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)

# 11a. organization_members
class OrganizationMember(Base):
    __tablename__ = "organization_members"
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    role = Column(String, CheckConstraint("role IN ('leader', 'member')"), nullable=False)
    joined_at = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "character_id"),)

# 12. jobs
class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    title = Column(String, nullable=False)
    salary = Column(Integer, nullable=False)
    schedule = Column(Text, nullable=False)
    required_skills = Column(Text, nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)

# 13. character_jobs
class CharacterJob(Base):
    __tablename__ = "character_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    started_at = Column(Integer, nullable=False)

# 14. accounts
class Account(Base):
    __tablename__ = "accounts"
    id = Column(String, primary_key=True, autoincrement=False)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    owner_type = Column(String, nullable=False) # character|organization|world
    owner_id = Column(String, nullable=False)
    balance = Column(Integer, nullable=False)
    created_at = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", name="_owner_uc"),)

# 15. transactions
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    game_timestamp = Column(Integer, nullable=False)
    from_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    to_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    amount = Column(Integer, CheckConstraint("amount > 0"), nullable=False)
    reason = Column(Text, nullable=False)
    event_id = Column(Integer, nullable=True)
    balance_from_after = Column(Integer, nullable=True)
    balance_to_after = Column(Integer, nullable=True)

# 16. world_objects
class WorldObject(Base):
    __tablename__ = "world_objects"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    object_type = Column(String, nullable=False)
    subtype = Column(String, nullable=True)
    owner_character_id = Column(String, ForeignKey("characters.id"), nullable=True)
    owner_organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    condition = Column(Integer, default=100, nullable=False)
    quantity = Column(Integer, nullable=False)
    object_metadata = Column(Text, nullable=False) # JSON string

# 17. resource_balances
class ResourceBalance(Base):
    __tablename__ = "resource_balances"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    resource_key = Column(String, nullable=False)
    owner_type = Column(String, nullable=False) # organization
    owner_id = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)

# 18. character_tasks
class CharacterTask(Base):
    __tablename__ = "character_tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    priority = Column(Integer, nullable=False)
    task_type = Column(String, nullable=False)
    target_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    source = Column(String, nullable=False)
    parameters = Column(Text, nullable=False) # JSON string
    created_at = Column(Integer, nullable=False)
    started_at = Column(Integer, nullable=True)
    ends_at = Column(Integer, nullable=True)
    completed_at = Column(Integer, nullable=True)

# 19. world_events
class WorldEvent(Base):
    __tablename__ = "world_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    game_timestamp = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    actor_id = Column(String, nullable=True)
    target_id = Column(String, nullable=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    payload = Column(Text, nullable=False) # JSON string

# 21. relationships
class Relationship(Base):
    __tablename__ = "relationships"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    character_a = Column(String, ForeignKey("characters.id"), nullable=False)
    character_b = Column(String, ForeignKey("characters.id"), nullable=False)
    trust = Column(Float, default=0.0, nullable=False)
    affection = Column(Float, default=0.0, nullable=False)
    respect = Column(Float, default=0.0, nullable=False)
    fear = Column(Float, default=0.0, nullable=False)
    anger = Column(Float, default=0.0, nullable=False)
    attraction = Column(Float, default=0.0, nullable=False)
    romantic_interest = Column(Float, default=0.0, nullable=False)
    familiarity = Column(Float, default=0.0, nullable=False)
    updated_at = Column(Integer, default=0, nullable=False)
    __table_args__ = (
        CheckConstraint("character_a < character_b"),
        CheckConstraint("trust BETWEEN -100 AND 100"),
        CheckConstraint("affection BETWEEN -100 AND 100"),
        CheckConstraint("respect BETWEEN -100 AND 100"),
        CheckConstraint("fear BETWEEN -100 AND 100"),
        CheckConstraint("anger BETWEEN -100 AND 100"),
        CheckConstraint("attraction BETWEEN -100 AND 100"),
        CheckConstraint("romantic_interest BETWEEN -100 AND 100"),
        CheckConstraint("familiarity BETWEEN -100 AND 100"),
        UniqueConstraint("world_id", "character_a", "character_b"),
    )

# 22. relationship_events
class RelationshipEvent(Base):
    __tablename__ = "relationship_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    character_a = Column(String, ForeignKey("characters.id"), nullable=False)
    character_b = Column(String, ForeignKey("characters.id"), nullable=False)
    event_type = Column(String, nullable=False)
    impact = Column(Float, nullable=False)
    event_id = Column(Integer, ForeignKey("world_events.id"), nullable=True)
    game_timestamp = Column(Integer, nullable=False)

# 23. world_snapshots
class WorldSnapshot(Base):
    __tablename__ = "world_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    game_timestamp = Column(Integer, nullable=False)
    created_real = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)

# 24. org_laws
class OrgLaw(Base):
    __tablename__ = "org_laws"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    law_key = Column(String, nullable=False)
    enacted_by_character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    enacted_day = Column(Integer, nullable=False)
    enacted_at = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("world_id", "organization_id"),)

# 25. org_law_violations
class OrgLawViolation(Base):
    __tablename__ = "org_law_violations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    law_key = Column(String, nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    game_day = Column(Integer, nullable=False)
    game_timestamp = Column(Integer, nullable=False)
    fine_paid = Column(Integer, nullable=False, default=0)
    event_id = Column(Integer, ForeignKey("world_events.id"), nullable=True)
    __table_args__ = (
        UniqueConstraint("world_id", "organization_id", "law_key", "character_id", "game_day"),
    )

# 26. memories (M4, SPEC §55)
class Memory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("world_events.id"), nullable=False)
    memory_type = Column(String, nullable=False)
    importance = Column(Integer, nullable=False)
    emotional_valence = Column(Float, nullable=False, default=0.0)
    summary = Column(Text, nullable=False)
    created_at = Column(Integer, nullable=False)
    last_recalled_at = Column(Integer, nullable=True)

# 27. ai_requests (M4, SPEC §50-51)
class AiRequest(Base):
    __tablename__ = "ai_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=True)
    task = Column(String, nullable=False)
    context = Column(JSON, nullable=False, default=dict)
    priority = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(Integer, nullable=False)
    processed_at = Column(Integer, nullable=True)
    game_timestamp = Column(Integer, nullable=False)

# 28. dialogue_turns (M4, SPEC §49/104-adjacent journal)
class DialogueTurn(Base):
    __tablename__ = "dialogue_turns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    session_id = Column(String, nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    game_timestamp = Column(Integer, nullable=False)
    request_id = Column(Integer, ForeignKey("ai_requests.id"), nullable=True)

# 29. users (M5, SPEC §81/104)
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")  # user|moderator|admin|developer (§82)
    age_confirmed = Column(Boolean, nullable=False, default=False)  # П4: 18+
    created_at = Column(Integer, nullable=False)

# 30. character_goals (M5, SPEC §62)
class CharacterGoal(Base):
    __tablename__ = "character_goals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    goal_type = Column(String, nullable=False)
    params = Column(JSON, nullable=False, default=dict)
    status = Column(String, nullable=False, default="queued")  # queued|active|done|failed|cancelled
    deadline_day = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=False)
    task_id = Column(String, ForeignKey("character_tasks.id"), nullable=True)
    created_at = Column(Integer, nullable=False)
    updated_at = Column(Integer, nullable=False)

# 31. dialogue_sessions (M5, SPEC §63)
class DialogueSession(Base):
    __tablename__ = "dialogue_sessions"
    id = Column(String, primary_key=True)
    world_id = Column(String, ForeignKey("worlds.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    npc_id = Column(String, ForeignKey("characters.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    started_at = Column(Integer, nullable=False)
    ended_at = Column(Integer, nullable=True)
    context = Column(JSON, nullable=False, default=dict)

# 32. dialogue_messages (M5, SPEC §63-64)
class DialogueMessage(Base):
    __tablename__ = "dialogue_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("dialogue_sessions.id"), nullable=False)
    sender = Column(String, nullable=False)  # user|npc|system
    content = Column(Text, nullable=False)
    suggested_responses = Column(JSON, nullable=True)
    game_timestamp = Column(Integer, nullable=False)

def create_engine_factory(settings: Settings):
    engine = create_engine(
        f"sqlite:///{settings.persistence.db_path}",
        connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine

def bootstrap(engine, settings: Settings, seed: int):
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Schema meta
        session.merge(SchemaMeta(key="version", value="0.5.0"))

        # World
        world_id = settings.world.world_id
        world = World(
            id=world_id,
            seed=seed,
            start_real=settings.world.start_real_timestamp,
            config_sha256=settings.world.config_sha256
        )
        session.merge(world)

        # World Clock
        clock = WorldClock(
            world_id=world_id,
            game_timestamp=0,
            time_scale=settings.ticks.time_scale,
            is_paused=False,
            last_real_timestamp=datetime.utcnow().isoformat()
        )
        session.merge(clock)

        session.commit()
