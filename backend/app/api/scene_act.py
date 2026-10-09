"""#87 [alpha-v1][A5]: deterministic free-text scene-action interpreter.

PURE text→intent module: no Session, no DB, no network, no LLM. Dev runs with
llm.enabled=false and POST /scene/act never calls a model on ANY path — there
is no LLM anywhere in this slice (spec: "LLM/interpreter cannot write DB/world
directly"). The interpreter receives only the raw text and returns a dict or
None; every mutation happens later, exclusively through the existing
authoritative paths in app.py: validate()+enqueue_task() (the POST /actions
path) and toggle_wear() (the POST /wear path).

Catalog (small, explicit, alpha — pinned by tests/m154):

- look      «осмотреться», «посмотреть вокруг», "look"  → pure read, no task
- move      «идти к X», «пойти в X», «дойти до X»        → grounded MOVE
- socialize «поговорить с X», «пообщаться с X»           → SOCIALIZE (the
            validator stays authoritative for target selection)
- sleep     «поспать», «отдохнуть», "sleep"              → SLEEP
- eat       «поесть», "eat"                              → EAT
- drink     «попить», «выпить воды», "drink"             → DRINK
- idle      «ничего не делать», «постоять», «подождать»  → IDLE
- buy       «купить X» (item via intent.py synonyms)     → BUY_ITEM
- wear      «надеть X»                                   → toggle_wear (worn on)
- take_off  «снять X»                                    → toggle_wear (worn off)

Precedence is the match order below (first hit wins): explicit observation
outranks movement («пойти осмотреться» → look), and a purpose phrase outranks
the movement verb inside it («пойти поесть» → eat). Word boundaries prevent
stem collisions («ждать» never matches inside «подождать»).

Exclusions (documented, tested — out of scope for alpha): arbitrary object
creation, new economy/relationship rules, skill system, universal action
language, direct LLM state mutation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------- catalog + exclusions (module data; pinned by tests/m154) ----------

SCENE_ACTIONS: List[str] = [
    "look", "move", "socialize", "sleep", "eat", "drink", "idle", "buy",
    "wear", "take_off",
]

SCENE_EXCLUDED: List[str] = [
    "arbitrary object creation",
    "new economy or relationship rules",
    "skill system",
    "universal action language",
    "direct LLM state mutation",
]

# User-facing phrasing per intent, reused in the unsupported explanation.
SCENE_HINTS: Dict[str, str] = {
    "look": "осмотреться / look",
    "move": "идти к X / пойти в X",
    "socialize": "поговорить с X",
    "sleep": "поспать / отдохнуть",
    "eat": "поесть",
    "drink": "попить / выпить воды",
    "idle": "ничего не делать / подождать",
    "buy": "купить X",
    "wear": "надеть X",
    "take_off": "снять X",
}

UNSUPPORTED_DETAIL = (
    "Действие не распознано. Каталог alpha: "
    + "; ".join(SCENE_HINTS[name] for name in SCENE_ACTIONS)
    + "."
)

# 018 (§28) wearables: mention fragment → object_type (inflected RU forms
# included; matched as substrings of the mention, longest first).
WEARABLE_SYNONYMS: Dict[str, str] = {
    "jacket": "jacket", "куртка": "jacket", "куртку": "jacket",
    "куртки": "jacket", "куртке": "jacket",
    "boots": "boots", "сапоги": "boots", "сапог": "boots",
    "сапогах": "boots", "ботинки": "boots", "ботинках": "boots",
    "hat": "hat", "шляпа": "hat", "шляпу": "hat", "шляпы": "hat",
    "шапка": "hat", "шапку": "hat", "шапки": "hat",
}

# ---------- intent regexes (word-bounded; order = precedence) ----------

_LOOK_RE = re.compile(
    r"\b(осмотреться|озмотреться|оглядеться|посмотреть вокруг|look|look around)\b")
_SOCIAL_RE = re.compile(r"\b(поговорить|пообщаться|поболтать|talk|chat)\b")
_TAKE_OFF_RE = re.compile(r"\b(снять|сними|снять с себя|take off|remove)\b")
_WEAR_RE = re.compile(r"\b(надеть|надень|одеть|wear|put on)\b")
_BUY_RE = re.compile(r"\b(купить|куплю|приобрести|buy)\b")
_EAT_RE = re.compile(r"\b(поесть|поедим|перекусить|пообедать|eat)\b")
_DRINK_RE = re.compile(r"\b(попить|выпить|пить|drink)\b")
_SLEEP_RE = re.compile(r"\b(поспать|отдохнуть|спать|sleep|rest)\b")
_MOVE_RE = re.compile(
    r"\b(идти|пойти|пойди|иди|дойти|сходить|подойти|go to|walk|move to)\b")
_IDLE_RE = re.compile(r"\b(ничего не делать|постоять|подождать|ждать|wait|idle)\b")

# Filler stripped from a movement phrase before destination grounding.
_MOVE_PREFIX_RE = re.compile(r"^(?:к|ко|в|во|на|до|у|по|туда|обратно)\s+")

_SOCIAL_TARGET_RE = re.compile(
    r"\b(?:поговорить|пообщаться|поболтать|talk|chat)\s+(?:со|с|with|to)?\s*(.*)$")
_WEAR_TARGET_RE = re.compile(r"\b(?:надеть|надень|одеть|wear|put on)\s+(.+)$")
_TAKE_OFF_TARGET_RE = re.compile(r"\b(?:снять|сними|take off|remove)\s+(.+)$")


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _clean_fragment(fragment: str) -> str:
    return (fragment or "").strip().strip(".,!?;:«»\"'").strip()


def _extract_move_destination(normalized: str, match) -> str:
    rest = _clean_fragment(normalized[match.end():])
    prev = None
    while prev != rest:
        prev = rest
        rest = _clean_fragment(_MOVE_PREFIX_RE.sub("", rest, count=1))
    return rest


def _extract_social_target(normalized: str) -> str:
    m = _SOCIAL_TARGET_RE.search(normalized)
    return _clean_fragment(m.group(1)) if m else ""


def _wearable_type(mention: str) -> Optional[str]:
    for word in sorted(WEARABLE_SYNONYMS, key=len, reverse=True):
        if word in mention:
            return WEARABLE_SYNONYMS[word]
    return None


def interpret_scene_action(text: str) -> Optional[Dict[str, Any]]:
    """Pure text → intent dict, or None when the text is unsupported.

    No Session parameter and no I/O of any kind: this function cannot read
    or write the DB/world, and this slice contains no LLM call at all.
    """
    normalized = _normalize(text)
    if not normalized:
        return None

    m = _LOOK_RE.search(normalized)
    if m:
        return {"intent": "look", "params": {}}

    m = _SOCIAL_RE.search(normalized)
    if m:
        return {"intent": "socialize",
                "params": {"target_text": _extract_social_target(normalized)}}

    m = _TAKE_OFF_RE.search(normalized)
    if m:
        tm = _TAKE_OFF_TARGET_RE.search(normalized)
        mention = _clean_fragment(tm.group(1)) if tm else ""
        return {"intent": "take_off",
                "params": {"object_text": mention,
                           "wearable": _wearable_type(mention)}}

    m = _WEAR_RE.search(normalized)
    if m:
        tm = _WEAR_TARGET_RE.search(normalized)
        mention = _clean_fragment(tm.group(1)) if tm else ""
        return {"intent": "wear",
                "params": {"object_text": mention,
                           "wearable": _wearable_type(mention)}}

    m = _BUY_RE.search(normalized)
    if m:
        # Reuse intent.py's deterministic item-key mapping (work order: the
        # mapping already exists there — do not fork it).
        from app.api.intent import _extract_item_key

        return {"intent": "buy", "params": {"item": _extract_item_key(normalized)}}

    m = _EAT_RE.search(normalized)
    if m:
        return {"intent": "eat", "params": {}}

    m = _DRINK_RE.search(normalized)
    if m:
        return {"intent": "drink", "params": {}}

    m = _SLEEP_RE.search(normalized)
    if m:
        return {"intent": "sleep", "params": {}}

    m = _MOVE_RE.search(normalized)
    if m:
        return {"intent": "move",
                "params": {"destination_text": _extract_move_destination(normalized, m)}}

    m = _IDLE_RE.search(normalized)
    if m:
        return {"intent": "idle", "params": {}}

    return None


def ground_social_target(
    candidates: List[Tuple[str, str]], mention: str
) -> Tuple[str, Optional[str], Union[str, List[str], None]]:
    """Deterministic name resolution over the characters present at the scene.

    `candidates` are (id, display_name) pairs; the mention is the fragment
    after the verb/preposition. Match tiers, strongest first: exact full
    name → full-name-substring (either direction) → token prefix of ≥4 chars
    (handles RU inflection: «Смирновым» starts with «Смирнов»). Returns
    ("ok", id, name) when unique; ("ambiguous", None, [names]) when several
    candidates tie; ("none", None, [present names]) when nothing matches.
    """
    names = [name for _cid, name in candidates]
    m = _clean_fragment((mention or "").lower())
    if not m:
        return ("ambiguous", None, names)

    exact: List[Tuple[str, str]] = []
    contains: List[Tuple[str, str]] = []
    prefix: List[Tuple[str, str]] = []
    for cid, full in candidates:
        f = (full or "").strip().lower()
        if not f:
            continue
        if f == m:
            exact.append((cid, full))
        elif f in m or m in f:
            contains.append((cid, full))
        else:
            tokens = [t for t in f.split() if len(t) >= 4]
            if len(m) >= 4 and any(
                m.startswith(t) or t.startswith(m) for t in tokens
            ):
                prefix.append((cid, full))

    tiers = exact or contains or prefix
    if not tiers:
        return ("none", None, names)
    if len(tiers) > 1:
        return ("ambiguous", None, [name for _i, name in tiers])
    cid, name = tiers[0]
    return ("ok", cid, name)
