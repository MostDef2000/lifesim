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
    User,
    WorldClock,
    WorldEvent,
    WorldObject,
)
from app.visual.style_presets import NUDE_FRAMING

# #205 (020 §Outfit / nudity control): per-character outfit modes. "auto"
# (the default, and the reading for NULL/unknown values on legacy rows)
# appends one concrete-garment pool item since #205d (pre-#205c it appended
# nothing — prompt bytes were identical to pre-#205).
_OUTFIT_MODES = ("auto", "casual", "formal", "nude")

# W205d (owner feedback): generic "everyday clothes" loses to the uncensored
# LoRA prior — auto portraits rendered naked while the nude mode rendered
# clothed. Concrete garments win (proven by casual); the pool is seed-selected
# in the portrait route for per-regeneration outfit variety. All pool items
# are adult + "fully clothed".
# #227 (prod probe): item 0 says "button-front" — the bare "fitted blouse"
# rendered as a cropped tank with exposed midriff; the closure detail anchors
# away from camisole/tank (same concrete-garment lesson as W205d).
_AUTO_OUTFIT_POOL = (
    "wearing a fitted button-front blouse and jeans, fully clothed",
    "wearing a summer dress, fully clothed",
    "wearing a cozy sweater and a long skirt, fully clothed",
    "wearing a button-up shirt and trousers, fully clothed",
)


def _effective_outfit_mode(session: Session, character) -> str:
    """#205: raw characters.outfit -> effective mode.

    Normalization rules (all documented invariants of 020):
    - NULL / unknown / blank value -> "auto" (legacy rows, hand-made rows:
      EXACTLY the pre-#205 prompt behavior);
    - "nude" requires the OWNING user to have age_confirmed (П4 18+, stored
      on users and enforced at registration). No owner (NPC — the lazy
      canonical portrait GET can be triggered by any authenticated user) or
      an owner without the flag collapses to "auto", so the nude fragment
      can only ever be produced from an age-confirmed owner's stored state;
    - worn clothing takes precedence at PROMPT level (build_prompt): an
      explicitly worn WorldObject means the character is dressed in EVERY
      mode, including nude — worn items always win.
    """
    mode = (getattr(character, "outfit", None) or "auto").strip().lower()
    if mode not in _OUTFIT_MODES:
        return "auto"
    if mode == "nude":
        owner_id = character.user_id
        if owner_id is None:
            return "auto"  # NPC: no owning user -> never nude
        owner = session.get(User, owner_id)
        if owner is None or not owner.age_confirmed:
            return "auto"
    return mode


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
# lora.available=False — no extra keys in Phase 1 (LoRA and the full-body
# preset are later phases). Per the amended spec (Phase 2b, #227) the
# negative-prompt transport contract is IN scope: compose_negative() below
# carries the canonical negative from the profile to the transport; the
# profile schema itself is unchanged.
_PROFILE_CASTING_PRESET = (
    # #227 (prod probe): crown clipped flush to the top frame and crop crept
    # to waist/upper hip despite the chest-up brief. Three named anchors:
    # chest-up framing, the whole head well inside the frame (the #205b
    # margin phrase kept verbatim), and an explicit nothing-below-the-chest
    # clause. One compact phrase — prompt bloat degrades adherence.
    "Chest-up portrait, entire head well inside the frame with clear margin "
    "above the hair, nothing below the chest visible, "
    "neutral warm-gray studio, soft frontal beauty lighting"
)
# W205b (owner feedback 2026-10-16): "head fully in frame" alone still crops
# faces at guidance 3.5 — the stronger compositional anchor names the margin
# explicitly (clear space above the hair).
_PROFILE_SCENE_PRESET = "natural environment lighting, candid composition"
_PROFILE_CANONICAL_NEGATIVE = (
    "child, teen, teenage, underage, minor, loli, lolicon, shota, kid, "
    "young girl, young boy, real person, real people, celebrity, politician, "
    "photograph of a real human"
)
# #227 Phase 2b (prod probes): prompt-side framing phrases cannot suppress
# the Flux waist-crop prior (0/3 this round, 0/4 in the preset round); the
# canvas bump is blocked (fp8 VRAM ceiling at 512, spec 019 excludes >512).
# This composition negative targets the observed midriff defect and rides
# every generation as the second part of the composed negative.
_COMPOSITION_NEGATIVE = "bare midriff, exposed stomach, navel"


def compose_negative(canonical_negative: str | None) -> str:
    """#227 Phase 2b: the negative-prompt string for a generation.

    Joins the canonical safety negative (from the visual profile guardrail;
    may be None or empty — descriptors without a player profile have no
    canonical part) with the composition constant. Deterministic order:
    canonical first, composition second; empty parts are dropped and no
    double commas are produced. The string rides every generation via the
    transport and is APPENDED to (never replaces) the adapter's technical
    negative floor (deploy/comfy_adapter.py build_negative).
    """
    parts = [(canonical_negative or "").strip(), _COMPOSITION_NEGATIVE]
    return ", ".join(part for part in parts if part)
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

# 020 P2: canonical structured-appearance key set (the 12 creator fields,
# stored on CharacterAppearance.appearance). The same set is validated for
# POST /characters (CharacterIn) and POST /visual/preview (PreviewIn) —
# see validate_appearance below; the creator form's camelCase draft keys
# (hairColor, extra) are mapped to these keys by the client.
APPEARANCE_FIELDS = frozenset({
    "face", "eyes", "nose", "lips", "hair", "hair_color", "skin",
    "height", "weight", "build", "chest", "features",
})

# 020 P2: appearance key → prompt-fragment template ({} = the stored
# value). Face keys compose the profile's face fragment, body keys the
# body fragment (spec 020 P2 mapping); tuple order is the deterministic
# fragment order. Only non-empty values contribute; with no contributing
# field at all the resolver falls back to the exact pre-020 bytes.
_APPEARANCE_FACE_TEMPLATES = (
    ("face", "{} face"),
    ("eyes", "{} eyes"),
    ("nose", "{} nose"),
    ("lips", "{} lips"),
    ("hair", "{} hair"),
    ("hair_color", "{} hair color"),
    ("skin", "{} skin"),
    ("features", "{}"),  # free-form extras, appended verbatim
)
_APPEARANCE_BODY_TEMPLATES = (
    ("height", "height {}"),
    ("weight", "weight {}"),
    ("build", "{} build"),
    ("chest", "{} chest"),
)

# #224: build-time RU→EN value mapping for STRUCTURED appearance values.
# The RU creator UI stores canonical values in Russian, but the Flux model
# ignores Russian in prompts (probe evidence: «рыжий»/«серые» rendered
# brown/ombré). _appearance_fragments maps KNOWN values — lookup key is
# value.strip().lower() — to a compact EN phrase at BUILD time only:
# - storage (character_appearances.appearance), validate_appearance and the
#   free-text looks/appearance_prompt are NEVER translated (user's words);
# - UNKNOWN values pass through VERBATIM (deterministic, documented);
# - GRAMMAR RULE: an EN value must compose grammatically with its template
#   suffix ("soft, gentle" + " face" -> "soft, gentle face";
#   "shoulder-length" + " hair"; "athletic" + " build"; the suffix-free
#   features slot takes whole noun phrases). Where no clean form exists the
#   slot stays unmapped — height/weight values are numeric/neutral and pass
#   through unchanged ("height 185" needs no mapping; adjective forms would
#   not compose with "height {}").
# Bounded seed set of the high-frequency creator vocabulary (#224); ё/е
# spellings included for common words. Extending it is additive and
# byte-pinned in tests/m224.
_APPEARANCE_VALUE_MAP: dict[str, dict[str, str]] = {
    "face": {
        "мягкие черты": "soft, gentle",
        "мягкие": "soft, gentle",
        "выразительные черты": "expressive",
        "выразительные": "expressive",
        "округлое лицо": "round",
        "округлые черты": "round",
        "угловатые черты": "sharp, angular",
        "резкие черты": "sharp, angular",
        "нордические черты": "nordic",
        "дружелюбное лицо": "friendly",
        "узкое лицо": "narrow",
        "широкое лицо": "broad",
    },
    "eyes": {
        "серые": "grey",
        "серый": "grey",
        "серые глаза": "grey",
        "карие": "brown",
        "карие глаза": "brown",
        "голубые": "blue",
        "зелёные": "green",
        "зеленые": "green",
        "синие": "blue",
        "чёрные": "black",
        "черные": "black",
        "янтарные": "amber",
        "ореховые": "hazel",
        "светлые": "light",
        "тёмные": "dark",
        "темные": "dark",
    },
    "nose": {
        "прямой": "straight",
        "небольшой": "small",
        "аккуратный": "neat",
        "орлиный": "aquiline",
        "курносый": "upturned",
        "широкий": "broad",
        "узкий": "narrow",
        "горбатый": "hooked",
    },
    "lips": {
        "полные": "full",
        "пухлые": "full",
        "тонкие": "thin",
        "узкие": "thin",
        "мягкие": "soft",
        "чётко очерченные": "well-defined",
        "четко очерченные": "well-defined",
        "бледные": "pale",
    },
    "hair": {
        "до плеч": "shoulder-length",
        "длинные": "long",
        "короткие": "short",
        "средней длины": "medium-length",
        "кудрявые": "curly",
        "волнистые": "wavy",
        "прямые": "straight",
        "короткая стрижка": "short-cropped",
    },
    "hair_color": {
        "рыжий": "ginger red",
        "рыжая": "ginger red",
        "рыжие": "ginger red",
        "русые": "dark blond",
        "русый": "dark blond",
        "блонд": "blond",
        "пепельные": "ash blond",
        "пепельный": "ash blond",
        "медные": "copper red",
        "каштановые": "brown",
        "каштановый": "brown",
        "шатен": "brown",
        "шатенка": "brown",
        "тёмные": "dark",
        "тёмные волосы": "dark",
        "темные": "dark",
        "чёрные": "black",
        "черные": "black",
        "светлые": "light blond",
        "седые": "grey",
    },
    "skin": {
        "светлая": "fair",
        "светлая кожа": "fair",
        "бледная": "pale",
        "бледная кожа": "pale",
        "смуглая": "olive",
        "тёмная": "dark",
        "темная": "dark",
        "загорелая": "tanned",
        "молочно-белая": "porcelain",
    },
    # numeric/neutral values ("170") pass through verbatim — see grammar rule
    "height": {
        "выше среднего": "above average",
        "ниже среднего": "below average",
    },
    "weight": {
        "выше среднего": "above average",
        "ниже среднего": "below average",
    },
    "build": {
        "атлетичное": "athletic",
        "атлетичный": "athletic",
        "атлетическая": "athletic",
        "худощавое": "slender",
        "худощавый": "slender",
        "стройное": "slim",
        "стройный": "slim",
        "мускулистое": "muscular",
        "хрупкое": "frail",
        "крепкое": "sturdy",
        "плотное": "stocky",
        "среднее": "average",
        "полноватое": "plump",
    },
    "chest": {
        "большая": "full",
        "средняя": "medium",
        "маленькая": "small",
        "скромная": "modest",
        "пышная": "full",
    },
    # suffix-free template ("{}"): whole noun phrases compose cleanly
    "features": {
        "веснушки": "freckles",
        "ямочки": "dimples",
        "родинка над губой": "a beauty mark above the lip",
        "родинка на щеке": "a mole on the cheek",
        "шрам на брови": "a scar through the eyebrow",
    },
}


def _appearance_prompt_value(field: str, value: str) -> str:
    """#224: the EN phrase for a KNOWN normalized appearance value, else
    the stored value verbatim (byte-identical pass-through). Build-time
    only — never applied to storage or free-text fields."""
    normalized = value.strip().lower()
    return _APPEARANCE_VALUE_MAP.get(field, {}).get(normalized) or value.strip()


def _appearance_fragments(appearance) -> tuple[str, str]:
    """020 P2: structured appearance → (face fragment, body fragment).

    ``appearance`` is a mapping or attribute object carrying the canonical
    creator keys (read via _profile_field, same duck-typing as the
    character_like input). Empty/absent fields contribute nothing; when no
    field contributes, BOTH fragments are "" so the caller falls through to
    the exact looks/trait derivation (byte-identical, m119 pins).

    #224: known structured values render through the build-time RU→EN
    value map (_appearance_prompt_value); unknown values pass through
    verbatim — storage and free-text fields are never translated.
    """
    if appearance is None:
        return "", ""
    face_parts = []
    body_parts = []
    for key, template in _APPEARANCE_FACE_TEMPLATES:
        value = _profile_field(appearance, key)
        if isinstance(value, str) and value.strip():
            face_parts.append(template.format(_appearance_prompt_value(key, value)))
    for key, template in _APPEARANCE_BODY_TEMPLATES:
        value = _profile_field(appearance, key)
        if isinstance(value, str) and value.strip():
            body_parts.append(template.format(_appearance_prompt_value(key, value)))
    return ", ".join(face_parts), ", ".join(body_parts)


def validate_appearance(value) -> dict | None:
    """020 P2: shared validation for the structured appearance input.

    Used by BOTH write paths that accept an appearance dict: POST
    /characters (CharacterIn.appearance) and POST /visual/preview
    (PreviewIn.appearance) — one contract, no drift. Rules: known keys
    only (APPEARANCE_FIELDS), every value a str, ≤120 chars per value
    (checked on the stripped value — the stored/canonical form), ≤12 keys
    total. Values are stripped and empty strings dropped (the creator
    client only sends filled fields; provided-but-empty == absent).
    Returns the cleaned mapping, or None for None/empty input. Raises
    ValueError with a client-safe message (routes map it to 422).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("appearance must be an object")
    if len(value) > len(APPEARANCE_FIELDS):
        raise ValueError("appearance accepts at most 12 fields")
    cleaned: dict[str, str] = {}
    for key, val in value.items():
        if key not in APPEARANCE_FIELDS:
            raise ValueError(f"unknown appearance field: {key}")
        if not isinstance(val, str):
            raise ValueError(f"appearance field {key} must be a string")
        text = val.strip()
        if not text:
            continue  # empty strings dropped
        if len(text) > 120:
            raise ValueError(f"appearance field {key} must be ≤120 chars")
        cleaned[key] = text
    return cleaned or None


def _character_appearance(session: Session, character_id: str) -> dict | None:
    """020 P2: stored canonical appearance for one character (pure read).

    Returns the CharacterAppearance.appearance dict when the row exists and
    carries a non-empty structured dict, else None (pre-020 characters, or
    a row that only holds the free-text prompt — that text already rides
    characters.looks, so the resolver needs nothing extra)."""
    from app.db.models import CharacterAppearance

    row = session.get(CharacterAppearance, character_id)
    if row is None or not isinstance(row.appearance, dict) or not row.appearance:
        return None
    return row.appearance


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


def build_visual_profile(character_like, appearance=None) -> dict:
    """019 Phase 1 (T1): deterministic canonical visual profile resolver.

    Pure function (R1): no RNG, no DB, no I/O. ``character_like`` is a
    mapping or object exposing the committed character state: age, sex,
    looks (may be None/empty), traits (mapping of trait key to 0-100 value;
    an iterable of key/value pairs is also accepted) and worn (optional list
    of worn clothing names; accepted for interface completeness but unused
    in Phase 1 — clothing renders via the separate ``wearing`` fragment).
    ``name`` is likewise accepted but not part of the profile (it renders in
    the prompt separately).

    020 P2: ``appearance`` (optional, same duck-typing — a mapping or object
    carrying the canonical creator keys, APPEARANCE_FIELDS) is the highest
    priority for the aspects its fields cover: appearance field >
    trait/sex-derived value. Face keys (face/eyes/nose/lips/hair/hair_color/
    skin/features) compose the appearance face fragment, body keys
    (height/weight/build/chest) the body fragment (body = appearance_body or
    the derived phrase); fragments join the same sections in the same order.
    The free-text looks fragment is never discarded: the face fragment
    combines the looks text FIRST (it is the primary description per the
    creator copy — free-text and structured fields complement, not exclude
    each other) followed by the appearance face fragment. When
    ``appearance`` is None or carries no non-empty field, the output is
    byte-identical to the pre-020 resolver (m119 pins). The frozen profile
    key set below never changes.

    Missing looks fall back to the neutral fixed phrase when no appearance
    face fields contribute either; style is composed from the trait mapping.
    Output matches the frozen schema exactly.
    """
    label = _profile_sex_label(_profile_field(character_like, "sex"))
    age = _profile_field(character_like, "age")
    age_part = f"{age}-year-old " if age is not None else ""

    looks_text = (_profile_field(character_like, "looks") or "").strip()
    appearance_face, appearance_body = _appearance_fragments(appearance)
    # free text first (primary), appearance fields append — combined, not
    # either/or; with neither present the neutral fixed phrase applies.
    face = ", ".join(
        part for part in (looks_text, appearance_face) if part
    ) or "natural, well-groomed features"
    body = appearance_body or (
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
            }, appearance=_character_appearance(session, player_character_id))

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
    # 018 (§28): worn clothing -> "wearing" list (empty -> one auto pool
    # item only, no worn-LIST fragment; pre-#205c this was byte-identical)
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
    # #205 (020 §Outfit / nudity control): the effective outfit mode travels
    # with the descriptor as an ADDITIVE top-level key (the m119 pins freeze
    # the visual_profile key set — this key lives on the descriptor itself,
    # which no pin fixes). Legacy/hand-made descriptors without the key read
    # as "auto" in build_prompt (which since #205d renders one seed-selected
    # concrete-garment pool item, absent outfit_auto_variant -> pool[0];
    # pre-#205c this read was byte-identical).
    descriptor["outfit_mode"] = _effective_outfit_mode(session, character)
    # 019 Phase 1 (T3): the canonical visual profile travels with the
    # descriptor (persisted on the VisualAsset scene_descriptor JSON — no
    # model/endpoint change). Built from the same committed state as above.
    # 020 P2: stored canonical appearance (character_appearances row, written
    # at creation) takes priority over trait derivation when present; absent
    # row → byte-identical pre-020 profile.
    descriptor["visual_profile"] = build_visual_profile({
        "name": f"{character.first_name} {character.last_name}",
        "age": character.age,
        "sex": character.sex,
        "looks": character.looks,
        "traits": _character_traits(session, character_id),
        "worn": list(wearing),
    }, appearance=_character_appearance(session, character_id))
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
        # 018 (§28): wearing fragment only when worn items exist.
        # 020 P3 stage 2: the API-level nude flag rides the descriptor as
        # ``nude_framing`` (set by the route layer) and takes a DEDICATED
        # fragment path: NUDE_FRAMING replaces EVERY clothing/outerwear
        # fragment (worn list AND the outfit-mode chain — the outfit_mode
        # "nude" branch emits "wearing nothing", which the stage-2 contract
        # bans from a nude positive). The key is absent on every other
        # path, so the chain below is byte-identical to pre-stage-2.
        wearing = descriptor.get("wearing") or []
        if descriptor.get("nude_framing"):
            base += f", {NUDE_FRAMING}"
        elif wearing:
            # #205 (020 §Outfit / nudity control): worn WorldObjects take
            # precedence in EVERY outfit mode (an explicitly worn item means
            # the character is dressed) — the nude_framing flag above is the
            # one deliberate exception (API-level per-request choice, it
            # replaces rather than competes with outfit fragments).
            base += f", wearing: {wearing}"
        else:
            # #205: the per-character outfit mode adds an explicit fragment
            # only when nothing is worn. "auto" (and legacy descriptors
            # without the outfit_mode key) share one fragment: since #205d
            # a concrete-garment pool item — pool[0] unless the descriptor
            # carries a seed-selected outfit_auto_variant (previously
            # nothing, byte-identical to pre-#205). The outfit_mode "nude"
            # fragment only ever reaches this point for an age-confirmed
            # owner: _effective_outfit_mode collapses every other case to
            # "auto" (guardrail phrase / canonical_negative above are
            # untouched, #119 invariant).
            mode = descriptor.get("outfit_mode") or "auto"
            if mode == "casual":
                # #205b (owner feedback 2026-10-16): "casual everyday clothes"
                # was too weak against the uncensored LoRA (topless renders) —
                # name the garments and state "fully clothed" outright.
                base += (
                    ", wearing casual clothes: a plain t-shirt and jeans, "
                    "fully clothed"
                )
            elif mode == "formal":
                base += ", wearing formal attire"
            elif mode == "nude":
                # W205d: symmetric to the garment fix — bare "nude" lost to
                # the modest style phrase; concrete nudity terms anchor the
                # render.
                base += ", nude, completely unclothed, wearing nothing"
            else:
                # W205d: concrete garments beat generic phrasing against the
                # LoRA prior; the route injects a seed-selected variant for
                # variety (absent key = pool[0], keeps direct build_prompt
                # callers deterministic).
                variant = descriptor.get("outfit_auto_variant")
                base += f", {variant if variant in _AUTO_OUTFIT_POOL else _AUTO_OUTFIT_POOL[0]}"
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
