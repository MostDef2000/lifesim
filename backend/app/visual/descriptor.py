"""M6 T5 (SPEC §67): Scene Descriptor + prompt builder.

Structured description is stored (§67: "Не хранить только prompt") — not just prompt.
Pure reads from DB, deterministic ordering (by id).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    Location,
    WorldClock,
    WorldEvent,
    WorldObject,
)


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def build_scene_descriptor(
    session: Session,
    world_id: str,
    location_id: str,
    event_id: int | None = None,
    camera: str = "wide",
) -> dict:
    """§67 structured scene description from committed world state (§66)."""
    location = session.get(Location, location_id)
    if location is None or location.world_id != world_id:
        raise LookupError(f"location not found: {location_id}")

    clock = session.get(WorldClock, world_id)
    game_ts = clock.game_timestamp if clock else 0

    characters = session.scalars(
        select(Character).where(
            Character.location_id == location_id,
            Character.alive == True,  # noqa: E712 — alive adults only (П4)
        ).order_by(Character.id)
    ).all()

    objects = session.scalars(
        select(WorldObject).where(
            WorldObject.location_id == location_id,
        ).order_by(WorldObject.id)
    ).all()

    event = session.get(WorldEvent, event_id) if event_id is not None else None
    event_part = None
    if event is not None and event.world_id == world_id:
        event_part = {"type": event.event_type, "actor_id": event.actor_id}

    return {
        "location": {"id": location.id, "name": location.name, "type": location.type},
        "characters": [
            {"id": c.id, "name": f"{c.first_name} {c.last_name}", "sex": c.sex, "age": c.age}
            for c in characters
        ],
        "objects": [{"id": o.id, "type": o.object_type} for o in objects],
        "weather": "clear",
        "time": {"day": game_ts // 1440, "hour": (game_ts % 1440) // 60,
                 "time_of_day": _time_of_day((game_ts % 1440) // 60)},
        "camera": camera,
        "event": event_part,
        "references": [],  # canonical portrait asset ids (§70), filled by caller
    }


def build_portrait_descriptor(session: Session, world_id: str, character_id: str) -> dict:
    character = session.get(Character, character_id)
    if character is None or character.world_id != world_id:
        raise LookupError(f"character not found: {character_id}")
    return {
        "location": None,
        "characters": [{"id": character.id, "name": f"{character.first_name} {character.last_name}",
                        "sex": character.sex, "age": character.age}],
        "objects": [],
        "weather": "clear",
        "time": None,
        "camera": "portrait",
        "event": None,
        "references": [],
    }


def build_prompt(descriptor: dict, visual_config_weather: str = "clear") -> str:
    """Deterministic descriptor -> prompt (R3). Same descriptor -> same prompt bytes."""
    if descriptor.get("camera") == "portrait":
        char = descriptor["characters"][0]
        return (
            f"Portrait of {char['name']}, {char['age']}-year-old {char['sex']} "
            f"adult fictional character, clean background, detailed face"
        )
    loc = descriptor["location"]
    parts = [
        f"{loc['type']} {loc['name']}",
        f"characters: {[c['name'] for c in descriptor['characters']]}",
        f"objects: {[o['type'] for o in descriptor['objects']]}",
        f"{descriptor.get('weather', visual_config_weather)} weather",
        f"{descriptor['time']['time_of_day']}",
        f"camera: {descriptor['camera']}",
    ]
    if descriptor.get("references"):
        parts.append(f"reference portraits: {descriptor['references']}")
    return ", ".join(parts)
