"""#90 [A8] Desire v1 — interpretation catalog + bounded opportunity planner.

Layer contract (spec of record: gh issue view 90):

- Desire is a SEPARATE long-term layer: it never touches the short-term
  ``CharacterGoal`` rows and never fulfils itself ("no magical direct
  fulfillment"). The planner below is read-only.
- ONE active desire per player-character. Enforced in the endpoint/service
  logic: creating a new active desire marks the previous one ``replaced``
  (with a ``replaced_by`` pointer) — SQLite has no partial unique index, so
  the invariant lives in code, and terminal rows stay as the audit trail.
- DETERMINISTIC, LLM-OFF: the keyword interpreter below IS the alpha
  implementation, not just a fallback. Dev runs with llm.enabled=false and
  this slice contains no model call on ANY path; the same code path serves
  as the safe fallback if an LLM interpreter is layered on top later (out
  of scope of the alpha catalog brevity — documented, tested).

Pure/purity split (mirrors app/social/roles.py and app/api/scene_act.py):

- ``interpret_desire(text)`` — PURE text→interpretation: no Session, no DB,
  no I/O. Unsupported/ambiguous/short input → explicit clarification with
  the supported catalog list (the ok:false-with-reason pattern of #87).
- ``build_opportunities(session, world_id, character_id, desire)`` —
  read-only planner over EXISTING authoritative systems only (characters
  present, derived roles, job, market-eligible inventory, home). It never
  writes: opportunities are descriptors; accepting one uses the existing
  authoritative mechanics (POST /dialogue/start, POST /actions, market
  endpoints). No basis → no opportunity (nothing is invented).

Catalog (fixed, small, alpha): relationship / public_role / business /
quiet_life. Keyword matching is substring-on-lowercased-text with RU stems
(inflection-tolerant: «дружбы» matches «дружб») plus EN words; precedence is
deterministic: the entry with the MOST matched keywords wins, a tie between
several entries is reported as ambiguous (clarification, no mutation).
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterJob,
    Job,
    Organization,
    WorldObject,
)

MIN_DESIRE_LENGTH = 3
MAX_OPPORTUNITIES = 5

# ---------- catalog (module data; pinned by tests/m157) ----------

CATALOG: Dict[str, Dict[str, Any]] = {
    "relationship": {
        "title_ru": "Отношения",
        "keywords": [
            "отношени", "дружб", "друз", "любов", "любим", "близост",
            "семь", "семей", "партнёр", "партнер", "встреча", "свидани",
            "общени", "знакомств", "роман",
            "relationship", "friend", "love", "family", "romance",
        ],
        "planner_hint": "npcs_at_location",
    },
    "public_role": {
        "title_ru": "Публичная роль",
        "keywords": [
            "признани", "уважен", "известн", "репутац", "лидер", "глав",
            "организац", "общин", "власт", "статус", "публичн", "авторитет",
            "role", "leader", "respect", "fame", "reputation", "community",
        ],
        "planner_hint": "roles_and_orgs",
    },
    "business": {
        "title_ru": "Дело и достаток",
        "keywords": [
            "бизнес", "дело", "деньг", "богатств", "заработ", "доход",
            "работ", "ремесл", "торг", "продаж", "рынок", "достаток",
            "карьер", "прибыл",
            "business", "money", "wealth", "job", "work", "trade", "market",
        ],
        "planner_hint": "work_and_market",
    },
    "quiet_life": {
        "title_ru": "Спокойная жизнь",
        "keywords": [
            "покой", "спокойн", "тих", "тишин", "уют", "дом", "отдых",
            "размеренн", "мирн", "гармон", "прощ",
            "quiet", "calm", "peace", "rest", "cozy",
        ],
        "planner_hint": "home_and_rest",
    },
}


def _catalog_listing() -> str:
    parts = []
    for key, entry in CATALOG.items():
        examples = ", ".join(entry["keywords"][:3])
        parts.append(f"{entry['title_ru']} ({examples}…)")
    return "; ".join(parts)


CLARIFICATION_CATALOG = (
    "Каталог alpha поддерживает: " + _catalog_listing() + "."
)

CLARIFICATION_UNSUPPORTED = (
    "Желание не распознано. " + CLARIFICATION_CATALOG
)

CLARIFICATION_AMBIGUOUS = (
    "Желание подходит сразу нескольким темам каталога — уточните его. "
    + CLARIFICATION_CATALOG
)

CLARIFICATION_TOO_SHORT = (
    "Желание слишком короткое — опишите его словами. " + CLARIFICATION_CATALOG
)


def interpret_desire(text: str) -> Dict[str, Any]:
    """Pure text → interpretation dict; never touches DB/world (no Session).

    Returns ``{"catalog_key", "title_ru", "confidence_note",
    "matched_keywords"}`` for a supported desire, or
    ``{"catalog_key": None, "clarification": ...}`` when the text is empty,
    shorter than MIN_DESIRE_LENGTH words-worthy, has no catalog keyword, or
    ties between several catalog entries (ambiguous).
    """
    normalized = " ".join((text or "").strip().lower().split())
    if len(normalized) < MIN_DESIRE_LENGTH:
        return {"catalog_key": None, "clarification": CLARIFICATION_TOO_SHORT}

    matches: Dict[str, List[str]] = {}
    for key, entry in CATALOG.items():
        hit = [kw for kw in entry["keywords"] if kw in normalized]
        if hit:
            matches[key] = hit
    if not matches:
        return {"catalog_key": None, "clarification": CLARIFICATION_UNSUPPORTED}

    best = max(len(v) for v in matches.values())
    winners = [k for k in matches if len(matches[k]) == best]
    if len(winners) > 1:
        return {"catalog_key": None, "clarification": CLARIFICATION_AMBIGUOUS}

    key = winners[0]
    keywords = matches[key]
    return {
        "catalog_key": key,
        "title_ru": CATALOG[key]["title_ru"],
        "confidence_note": "совпадения: " + ", ".join(keywords[:4]),
        "matched_keywords": keywords,
    }


# ---------- planner (read-only; existing systems ONLY) ----------

def _location_name(session: Session, location_id) -> str:
    from app.db.models import Location

    if location_id is None:
        return "—"
    loc = session.get(Location, int(location_id))
    return loc.name if loc is not None else f"#{location_id}"


def _npcs_at(session: Session, world_id: str, char: Character, limit: int) -> List[Character]:
    return (
        session.query(Character)
        .filter(
            Character.world_id == world_id,
            Character.alive.is_(True),
            Character.user_id.is_(None),  # NPCs only: dialogue is the mechanic
            Character.id != char.id,
            Character.location_id == char.location_id,
        )
        .order_by(Character.id)
        .limit(limit)
        .all()
    )


def _relationship_opportunities(
        session: Session, world_id: str, char: Character) -> List[Dict[str, Any]]:
    place = _location_name(session, char.location_id)
    opps = []
    for npc in _npcs_at(session, world_id, char, 2):
        name = f"{npc.first_name} {npc.last_name}".strip()
        opps.append({
            "id": f"npc:{npc.id}",
            "title": f"Поговорить с {name}",
            "reason": f"{npc.first_name} сейчас рядом ({place})",
            "source": "локация",
            "action": {"mechanic": "dialogue", "npc_id": npc.id},
            "link": "#/chat",
        })
    return opps


def _public_role_opportunities(
        session: Session, world_id: str, char: Character) -> List[Dict[str, Any]]:
    # Basis rule (honest): an org-role opportunity exists ONLY when an
    # organization actually exists in the world (or the character already
    # holds a derived org role). No org → no opportunity, nothing invented.
    from app.social.roles import derive_roles

    opps: List[Dict[str, Any]] = []
    org_roles = [r for r in derive_roles(session, world_id, char.id)
                 if r["id"] in ("org_leader", "org_member")]
    for role in org_roles[:1]:
        opps.append({
            "id": f"role:{char.id}",
            "title": f"Ваша публичная роль: {role['title']}",
            "reason": role["reason"],
            "source": role["source"],
            "action": {"mechanic": "read", "endpoint": f"/characters/{char.id}/roles"},
            "link": "#/profile",
        })
    org = (
        session.query(Organization)
        .filter_by(world_id=world_id)
        .order_by(Organization.id)
        .first()
    )
    if org is not None and not org_roles:
        opps.append({
            "id": f"org:{org.id}",
            "title": f"Познакомьтесь с «{org.name}»",
            "reason": (f"организация «{org.name}» действует на острове — "
                       f"публичные роли растут из участия"),
            "source": "организация",
            "action": {"mechanic": "read", "endpoint": f"/characters/{char.id}/roles"},
            "link": "#/profile",
        })
    return opps


def _business_opportunities(
        session: Session, world_id: str, char: Character) -> List[Dict[str, Any]]:
    opps: List[Dict[str, Any]] = []
    job = (
        session.query(Job)
        .join(CharacterJob, CharacterJob.job_id == Job.id)
        .filter(CharacterJob.character_id == char.id)
        .order_by(CharacterJob.id)
        .first()
    )
    if job is not None:
        away = job.location_id is not None and job.location_id != char.location_id
        reason = "работа приносит доход"
        if away:
            reason += " — рабочее место в другой локации, сначала дойдите туда"
        opps.append({
            "id": f"work:{job.id}",
            "title": f"Работать: {job.title}",
            "reason": reason,
            "source": "работа",
            "action": {"mechanic": "action", "action_type": "WORK"},
            "link": "#/world",
        })
    else:
        sellable = (
            session.query(WorldObject)
            .filter(
                WorldObject.world_id == world_id,
                WorldObject.owner_character_id == char.id,
                WorldObject.quantity >= 1,
            )
            .count()
        )
        if sellable:
            opps.append({
                "id": "market:inventory",
                "title": "Выставить вещь на рынок",
                "reason": f"в инвентаре {sellable} предм. — продажа приносит деньги",
                "source": "рынок",
                "action": {"mechanic": "read", "endpoint": f"/characters/{char.id}/inventory"},
                "link": "#/inventory",
            })
    return opps


def _quiet_life_opportunities(
        session: Session, world_id: str, char: Character) -> List[Dict[str, Any]]:
    # Basis rule: the character's own home row is the authoritative anchor.
    # No home → no opportunity (the client must not invent one either).
    if char.home_location_id is None:
        return []
    if char.home_location_id != char.location_id:
        return [{
            "id": f"home:{char.home_location_id}",
            "title": "Идти домой",
            "reason": f"дом ({_location_name(session, char.home_location_id)}) — тихое место",
            "source": "дом",
            "action": {"mechanic": "action", "action_type": "MOVE",
                       "params": {"location_id": char.home_location_id}},
            "link": "#/world",
        }]
    return [{
        "id": "rest:home",
        "title": "Отдохнуть дома",
        "reason": "вы уже дома — отдых восстановит силы",
        "source": "дом",
        "action": {"mechanic": "action", "action_type": "SLEEP"},
        "link": "#/world",
    }]


def build_opportunities(
    session: Session, world_id: str, character_id: str, desire
) -> List[Dict[str, Any]]:
    """Bounded (≤ MAX_OPPORTUNITIES) read-only opportunities for a desire.

    Queries ONLY existing systems (characters present, derived roles, job,
    owned objects, home). NEVER mutates: no add/commit/flush anywhere here —
    pinned by a DB-snapshot test. Unknown catalog key → [].
    """
    char = session.get(Character, character_id)
    if char is None or char.world_id != world_id:
        return []
    key = getattr(desire, "catalog_key", None)
    if key == "relationship":
        opps = _relationship_opportunities(session, world_id, char)
    elif key == "public_role":
        opps = _public_role_opportunities(session, world_id, char)
    elif key == "business":
        opps = _business_opportunities(session, world_id, char)
    elif key == "quiet_life":
        opps = _quiet_life_opportunities(session, world_id, char)
    else:
        opps = []
    return opps[:MAX_OPPORTUNITIES]
