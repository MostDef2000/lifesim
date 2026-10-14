"""M6 T5 (SPEC §67): Scene Descriptor + prompt builder.

Structured description is stored (§67: "Не хранить только prompt") — not just prompt.
Pure reads from DB, deterministic ordering (by id).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterTrait,
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


# 019 Phase 1 (T1): canonical visual profile. The schema below is FROZEN by
# specs/019-flux-baseline-quality (§ frozen profile schema): face/body/style,
# scenes.casting/scenes.scene, guardrail.phrase/canonical_negative and
# lora.available=False — no extra keys in Phase 1 (LoRA, full-body preset and
# a negative-prompt transport contract are later phases; the canonical
# negative stays stored on the profile until lane B moves the transport).
_PROFILE_CASTING_PRESET = (
    "Chest-up portrait, neutral warm-gray studio, soft frontal beauty lighting"
)
_PROFILE_SCENE_PRESET = "natural environment lighting, candid composition"
_PROFILE_CANONICAL_NEGATIVE = (
    "child, teen, teenage, underage, minor, loli, lolicon, shota, kid, "
    "young girl, young boy, real person, real people, celebrity, politician, "
    "photograph of a real human"
)
_PROFILE_DEFAULT_STYLE = "modest, casual everyday styling"
# Trait values are 0-100 (committed CharacterTrait rows); the upper third
# counts as high. Values below the threshold (and absent keys) contribute no
# style fragment — with no fragments at all the default styling applies.
_PROFILE_STYLE_HIGH_THRESHOLD = 67
_PROFILE_STYLE_HIGH_FRAGMENTS = (
    ("sociability", "sociable, outgoing presence"),
    ("discipline", "neat, put-together styling"),
    ("risk_tolerance", "bold, adventurous styling"),
)


def _profile_field(character_like, key: str, default=None):
    """Read a resolver input from a mapping or an attribute-bearing object."""
    if isinstance(character_like, dict):
        return character_like.get(key, default)
    return getattr(character_like, key, default)


def _profile_sex_label(sex) -> str:
    token = (sex or "").strip().upper()
    if token == "M":
        return "man"
    if token == "F":
        return "woman"
    return "adult"


def build_visual_profile(character_like) -> dict:
    """019 Phase 1 (T1): deterministic canonical visual profile resolver.

    Pure function (R1): no RNG, no DB, no I/O. ``character_like`` is a
    mapping or object exposing the committed character state: age, sex,
    looks (may be None/empty), traits (mapping of trait key to 0-100 value;
    an iterable of key/value pairs is also accepted) and worn (optional list
    of worn clothing names; accepted for interface completeness but unused
    in Phase 1 — clothing renders via the separate ``wearing`` fragment).
    ``name`` is likewise accepted but not part of the profile (it renders in
    the prompt separately).

    Missing looks fall back to neutral age/sex-appropriate phrasing; style is
    composed from the trait mapping. Output matches the frozen schema exactly.
    """
    label = _profile_sex_label(_profile_field(character_like, "sex"))
    age = _profile_field(character_like, "age")
    age_part = f"{age}-year-old " if age is not None else ""

    looks_text = (_profile_field(character_like, "looks") or "").strip()
    face = looks_text or "natural, well-groomed features"
    body = (
        f"healthy adult {label} build" if label != "adult"
        else "healthy adult build"
    )

    traits = _profile_field(character_like, "traits") or {}
    if not isinstance(traits, dict):
        traits = {key: value for key, value in traits}
    fragments = [
        fragment
        for key, fragment in _PROFILE_STYLE_HIGH_FRAGMENTS
        if isinstance(traits.get(key), (int, float))
        and traits[key] >= _PROFILE_STYLE_HIGH_THRESHOLD
    ]
    style = "; ".join(fragments) if fragments else _PROFILE_DEFAULT_STYLE

    return {
        "face": face,
        "body": body,
        "style": style,
        "scenes": {
            "casting": _PROFILE_CASTING_PRESET,
            "scene": _PROFILE_SCENE_PRESET,
        },
        "guardrail": {
            # unknown sex collapses to the plain adult phrase (no "adult adult")
            "phrase": (
                f"{age_part}fictional adult character"
                if label == "adult"
                else f"{age_part}fictional adult {label} character"
            ),
            "canonical_negative": _PROFILE_CANONICAL_NEGATIVE,
        },
        "lora": {"available": False},
    }


def _character_traits(session: Session, character_id: str) -> dict[str, int]:
    """Committed trait values for one character (pure read, 019 Phase 1)."""
    rows = session.scalars(
        select(CharacterTrait).where(CharacterTrait.character_id == character_id)
    ).all()
    return {row.trait_key: row.value for row in rows}


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
    # 019 Phase 1 (T3): the player's canonical visual profile is embedded as a
    # TOP-LEVEL descriptor key (the pinned player-part key set stays untouched)
    # and only when a player part exists — scenes without one keep the
    # pre-019 descriptor shape.
    player_part = None
    visual_profile = None
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
            visual_profile = build_visual_profile({
                "name": player_part["name"],
                "age": player.age,
                "sex": player.sex,
                "looks": player.looks,
                "traits": _character_traits(session, player_character_id),
                "worn": player_part["worn"],
            })

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

    scene_descriptor = {
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
    if visual_profile is not None:
        scene_descriptor["visual_profile"] = visual_profile
    return scene_descriptor


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
    # 019 Phase 1 (T3): the canonical visual profile travels with the
    # descriptor (persisted on the VisualAsset scene_descriptor JSON — no
    # model/endpoint change). Built from the same committed state as above.
    descriptor["visual_profile"] = build_visual_profile({
        "name": f"{character.first_name} {character.last_name}",
        "age": character.age,
        "sex": character.sex,
        "looks": character.looks,
        "traits": _character_traits(session, character_id),
        "worn": list(wearing),
    })
    return descriptor


def build_prompt(descriptor: dict, visual_config_weather: str = "clear") -> str:
    """Deterministic descriptor -> prompt (R3). Same descriptor -> same prompt bytes."""
    if descriptor.get("camera") == "portrait":
        char = descriptor["characters"][0]
        # 019 Phase 1 (T2): structured composition from the canonical visual
        # profile in FIXED section order (guardrail phrase, face, body, style,
        # casting preset); descriptors without an embedded profile (hand-made
        # fixtures, pre-019 rows) synthesize one from the identity facts so
        # the builder stays total and deterministic.
        profile = descriptor.get("visual_profile") or build_visual_profile(char)
        base = (
            f"Portrait of {char['name']}, {profile['guardrail']['phrase']}, "
            f"{profile['face']}, {profile['body']}, {profile['style']}, "
            f"{profile['scenes']['casting']}, clean background, detailed face"
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
    ]
    # 019 Phase 1 (T2): the scene view gains the profile styling and the
    # scene-lighting preset ahead of the environment/time composition, when
    # the descriptor carries a profile (requesting player present).
    profile = descriptor.get("visual_profile")
    if profile:
        parts.append(profile["style"])
        parts.append(profile["scenes"]["scene"])
    parts += [
        f"{descriptor.get('weather', visual_config_weather)} weather",
        f"{descriptor['time']['time_of_day']}",
        f"camera: {descriptor['camera']}",
    ]
    if descriptor.get("references"):
        parts.append(f"reference portraits: {descriptor['references']}")
    return ", ".join(parts)
