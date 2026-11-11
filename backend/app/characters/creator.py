"""020 wave 4 (issue #316): AI character-draft engine (creator drafting).

Pure module: prompt composition + STRICT validation of model output. No DB
access, no I/O, no state mutation — the constitution П2 amendment (2026-11-11)
admits creator drafting as a Tier 1/2 LLM use ONLY as optional editable
DRAFT proposals: every value passes server-side validation here, the
simulation state is never touched, and the manual creation path stays the
primary flow.

One LLM call per operation via the EXISTING configured transport
(build_transport(settings.llm)); no queued gateway (it writes AiRequest
rows), no retries, no repair loops, no persistence. The draft endpoint
(api/creator_routes.py) stays write-free.

Strict output schema (generate):
    name (≤100), sex "M"|"F", age int 18-99, the 12 appearance keys
    (str ≤120 each, English, image-generator-oriented), supplement
    (≤500, English, must NOT repeat anatomy/clothing), biography
    (≤2000, Russian), outfit_mode "auto"|"casual"|"formal" (NUDE is
    forbidden from AI — rejected), custom_clothing (≤500, English,
    concrete garments).

Validation failures raise ValueError with client-safe messages; the route
maps them to 502 (model output) or 422 (client request shape).
"""
from __future__ import annotations

import json

# Bounds (mirrored by the route-level request-shape checks and the tests).
IDEA_MAX = 500
NAME_MAX = 100
ANATOMY_MAX = 120
SUPPLEMENT_MAX = 500
BIOGRAPHY_MAX = 2000
CUSTOM_CLOTHING_MAX = 500
AGE_MIN = 18
AGE_MAX = 99

# Bound on the raw upstream response READ (rides the transport's optional
# max_chars param): any valid draft JSON fits far below this; a longer
# response is a runaway generation and is rejected before parsing.
MAX_RESPONSE_CHARS = 20_000

# The 12 canonical creator fields (same key set as storage /
# APPEARANCE_FIELDS in app.visual.descriptor — kept as an explicit tuple
# here so the draft schema is readable; the sets must stay identical).
ANATOMY_KEYS = (
    "face", "eyes", "nose", "lips", "hair", "hair_color", "skin",
    "height", "weight", "build", "chest", "features",
)

# Flat draft keys = the variant field allowlist (exactly the draft keys).
DRAFT_FIELDS = (
    "name", "sex", "age", *ANATOMY_KEYS,
    "supplement", "biography", "outfit_mode", "custom_clothing",
)

# NUDE is forbidden from AI drafts (the manual outfit path keeps it — the
# AI never proposes it; a nude value in the model output → 502).
AI_OUTFIT_MODES = ("auto", "casual", "formal")

_GENERATE_SCHEMA_HINT = (
    'a JSON object with exactly these keys: name (string, 1-100 chars), '
    'sex ("M" or "F"), age (integer 18-99), face, eyes, nose, lips, hair, '
    'hair_color, skin, height, weight, build, chest, features (short '
    'English strings, each ≤120 chars), supplement (English string ≤500 '
    'chars, may be empty), biography (Russian string ≤2000 chars), '
    'outfit_mode ("auto"|"casual"|"formal"), custom_clothing (English '
    'string ≤500 chars, may be empty)'
)

_VARIANT_SCHEMA_HINT = (
    'a JSON object with a single key "value" carrying the replacement '
    'value for the requested field'
)


def generate_schema_hint() -> str:
    """Schema hint for the generate operation (rides the transport's
    system message)."""
    return _GENERATE_SCHEMA_HINT


def variant_schema_hint() -> str:
    return _VARIANT_SCHEMA_HINT


def build_generate_prompt(idea: str) -> str:
    """The single generate-call user prompt (deterministic bytes)."""
    return (
        "You are a character designer for a fiction life-simulation game. "
        "Design ONE fictional adult character from the player's idea.\n\n"
        f"Player idea: {idea}\n\n"
        "Reply with STRICT JSON only (no markdown, no commentary) carrying "
        "EXACTLY these keys:\n"
        '- "name": full character name, 1-100 characters\n'
        '- "sex": "M" or "F"\n'
        '- "age": integer 18-99\n'
        '- "face", "eyes", "nose", "lips", "hair", "hair_color", "skin", '
        '"height", "weight", "build", "chest", "features": short ENGLISH '
        "image-generator-oriented descriptions, each ≤120 characters\n"
        '- "supplement": extra ENGLISH visual details NOT covered by the '
        "fields above and NOT clothing; must NOT repeat any anatomy or "
        "clothing text; ≤500 characters; may be empty\n"
        '- "biography": RUSSIAN-language backstory, ≤2000 characters\n'
        '- "outfit_mode": "auto", "casual" or "formal" (never "nude")\n'
        '- "custom_clothing": concrete ENGLISH garments, ≤500 characters; '
        "may be empty\n"
        "Rules: every character is a fictional adult (18+). Reply with the "
        "JSON object only."
    )


def build_variant_prompt(field: str, draft: dict) -> str:
    """The single variant-call prompt: replace ONE field of the given
    draft, everything else stays as-is."""
    return (
        "You are a character designer for a fiction life-simulation game. "
        "Below is the current character draft and ONE field to replace.\n\n"
        f"Current draft: {json.dumps(draft, ensure_ascii=False)}\n\n"
        f"Replace ONLY the field \"{field}\" with a new, different, "
        "well-fitting value of the same type and bounds.\n\n"
        "Reply with STRICT JSON only: a single object with one key "
        '"value" carrying the replacement for that field. Rules: every '
        "character is a fictional adult (18+); appearance fields are short "
        "ENGLISH image-generator descriptions; the biography is RUSSIAN; "
        "outfit_mode is never \"nude\"."
    )


def parse_llm_json(raw: str) -> dict:
    """Parse the raw transport response into a JSON object.

    Unparseable / non-object output raises ValueError (route → 502)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("model output is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("model output must be a JSON object")
    return data


def _require_str(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value.strip()


def _validate_name(value) -> str:
    text = _require_str(value, "name")
    if not text:
        raise ValueError("name must not be empty")
    if len(text) > NAME_MAX:
        raise ValueError(f"name must be ≤{NAME_MAX} chars")
    # Name rules here are DELIBERATELY weaker than create_player_character
    # (app/characters/player.py): that path enforces the non-empty first
    # token (space split) and world uniqueness at POST /characters; the
    # draft only bounds length — final validation happens at creation.
    return text


def _validate_sex(value) -> str:
    if value not in ("M", "F"):
        raise ValueError("sex must be M or F")
    return value


def _validate_age(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("age must be an integer")
    if not AGE_MIN <= value <= AGE_MAX:
        raise ValueError(f"age must be {AGE_MIN}-{AGE_MAX}")
    return value


def _validate_anatomy(field: str, value) -> str:
    text = _require_str(value, field)
    if len(text) > ANATOMY_MAX:
        raise ValueError(f"{field} must be ≤{ANATOMY_MAX} chars")
    return text


def _validate_supplement(value) -> str:
    text = _require_str(value, "supplement")
    if len(text) > SUPPLEMENT_MAX:
        raise ValueError(f"supplement must be ≤{SUPPLEMENT_MAX} chars")
    return text


def _validate_biography(value) -> str:
    text = _require_str(value, "biography")
    if len(text) > BIOGRAPHY_MAX:
        raise ValueError(f"biography must be ≤{BIOGRAPHY_MAX} chars")
    return text


def _validate_outfit_mode(value) -> str:
    # NUDE is forbidden from AI drafts (the manual path keeps it).
    if value not in AI_OUTFIT_MODES:
        raise ValueError("outfit_mode must be auto|casual|formal")
    return value


def _validate_custom_clothing(value) -> str:
    text = _require_str(value, "custom_clothing")
    if len(text) > CUSTOM_CLOTHING_MAX:
        raise ValueError(f"custom_clothing must be ≤{CUSTOM_CLOTHING_MAX} chars")
    return text


_FIELD_VALIDATORS = {
    "name": _validate_name,
    "sex": _validate_sex,
    "age": _validate_age,
    "supplement": _validate_supplement,
    "biography": _validate_biography,
    "outfit_mode": _validate_outfit_mode,
    "custom_clothing": _validate_custom_clothing,
}


def validate_field_value(field: str, value):
    """Validate ONE draft field value (the variant response path). Unknown
    fields raise ValueError (route → 502 for model output)."""
    if field in ANATOMY_KEYS:
        return _validate_anatomy(field, value)
    validator = _FIELD_VALIDATORS.get(field)
    if validator is None:
        raise ValueError(f"unknown draft field: {field}")
    return validator(value)


def validate_draft(data) -> dict:
    """Strict FULL-draft validation — used for BOTH the generate output
    and the incoming variant draft (the caller must carry the complete
    current draft; same bounds everywhere).

    Unknown keys, missing keys, wrong types and out-of-range values raise
    ValueError with client-safe messages. Returns the cleaned draft with
    the canonical key order (DRAFT_FIELDS)."""
    if not isinstance(data, dict):
        raise ValueError("draft must be an object")
    unknown = sorted(set(data) - set(DRAFT_FIELDS))
    if unknown:
        raise ValueError(f"unknown draft keys: {unknown}")
    missing = [field for field in DRAFT_FIELDS if field not in data]
    if missing:
        raise ValueError(f"missing draft keys: {missing}")
    return {field: validate_field_value(field, data[field])
            for field in DRAFT_FIELDS}
