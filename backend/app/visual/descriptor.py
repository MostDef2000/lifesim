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
    location_id: int,
    event_id: int | None = None,
    camera: str = "wide",
    player_character_id: str | None = None,
) -> dict:
    """§67 structured scene description from committed world state (§66).

    #86 (A4): player_character_id enriches the descriptor with the requesting
    player's appearance/outfit (`player` section) and their canonical portrait
    reference — all pure reads of committed state; default None keeps the
    pre-A4 shape for existing callers.
    """
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

    # #86 (A4): the requesting player's appearance/outfit — committed state
    # only (Character.looks self-description + worn wearable WorldObjects
    # per 018 §28 object_metadata). Pure read; nothing here mutates.
    player_part = None
    if player_character_id is not None:
        player = session.get(Character, player_character_id)
        if player is not None and player.world_id == world_id:
            from app.social.clothing import worn_items

            player_part = {
                "id": player.id,
                "name": f"{player.first_name} {player.last_name}",
                "sex": player.sex,
                "age": player.age,
                "looks": player.looks or "",
                "worn": [
                    r.object_type
                    for r in worn_items(session, world_id, player_character_id)
                ],
            }

    # #86 (A4): the player's canonical portrait asset id (§70) — the same
    # query /visual/characters/{cid}/portrait serves. None → references [].
    references: list[int] = []
    if player_character_id is not None:
        from app.db.models import VisualAsset

        canonical = (
            session.query(VisualAsset)
            .filter_by(
                world_id=world_id, asset_type="portrait",
                character_id=player_character_id, canonical=True,
            )
            .first()
        )
        if canonical is not None:
            references = [canonical.id]

    # 010 (§71, R6): weather from the world's current day row; without a row
    # the descriptor stays byte-identical to the pre-weather pin ('clear').
    weather_str = "clear"
    from app.db.models import WeatherState
    from app.db.models import WorldClock as _WeatherClock

    clock = session.query(_WeatherClock).filter_by(world_id=world_id).first()
    if clock is not None:
        wrow = (
            session.query(WeatherState)
            .filter_by(world_id=world_id, day=clock.game_timestamp // 1440)
            .first()
        )
        if wrow is not None:
            from app.simulation.weather import describe_weather

            weather_str = describe_weather(wrow)

    return {
        "location": {"id": location.id, "name": location.name, "type": location.type},
        "characters": [
            {"id": c.id, "name": f"{c.first_name} {c.last_name}", "sex": c.sex, "age": c.age}
            for c in characters
        ],
        "objects": [{"id": o.id, "type": o.object_type} for o in objects],
        "weather": weather_str,
        "time": {"day": game_ts // 1440, "hour": (game_ts % 1440) // 60,
                 "time_of_day": _time_of_day((game_ts % 1440) // 60)},
        "camera": camera,
        "event": event_part,
        "player": player_part,  # #86 (A4): None without player_character_id
        "references": references,  # §70 canonical portrait asset ids
    }


def build_portrait_descriptor(session: Session, world_id: str, character_id: str) -> dict:
    character = session.get(Character, character_id)
    if character is None or character.world_id != world_id:
        raise LookupError(f"character not found: {character_id}")
    # 018 (§28): worn clothing -> "wearing" list (empty -> byte-identical)
    from app.social.clothing import wearing_phrases

    descriptor = {
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
    wearing = wearing_phrases(session, world_id, character_id)
    if wearing:
        descriptor["wearing"] = wearing
    return descriptor


def build_prompt(descriptor: dict, visual_config_weather: str = "clear") -> str:
    """Deterministic descriptor -> prompt (R3). Same descriptor -> same prompt bytes."""
    if descriptor.get("camera") == "portrait":
        char = descriptor["characters"][0]
        base = (
            f"Portrait of {char['name']}, {char['age']}-year-old {char['sex']} "
            f"adult fictional character, clean background, detailed face"
        )
        # 018 (§28): wearing fragment only when worn items exist
        wearing = descriptor.get("wearing") or []
        if wearing:
            base += f", wearing: {wearing}"
        return base
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
