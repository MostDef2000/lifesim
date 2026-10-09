"""#89 [A7] Social Roles v1 — deterministic public role derivation.

Derive-on-read read model: roles are computed from authoritative world rows
ONLY (no LLM, no new tables, no writes). Every role has a deterministic
authoritative basis; an unsupported or fuzzy role is NOT invented.

Catalog (fixed, small):

- ``newcomer``   — character has spent fewer than NEWCOMER_DAYS days in the
  world (Characters.created_at vs the world clock). Source: «недавно на
  острове».
- ``occupation`` — the character's job (CharacterJob → Job.title, fallback
  Characters.occupation_id). Source: «работа: <title>».
- ``org_leader`` / ``org_member`` — OrganizationMember.role (leader|member)
  in an Organization. Source: the organization name.
- ``offender``   — a Crime row with status reported|resolved (a publicly
  known offense). An *unreported* crime is a secret fact and must NOT grant
  a public role.

EXCLUDED_ROLES — roles deliberately absent from the catalog:

- ``partner`` — the schema has no authoritative marriage/partnership source
  (no marriage/partner tables). Relationship.romantic_interest is a sentiment
  scalar, not a partnership fact; deriving «партнёр» from it would invent a
  fuzzy role (spec: «unsupported/fuzzy role is not invented»).

Traceability contract (spec: «role changes are traceable»): this is a
derive-on-read model — it keeps no event log and never writes. The change
log IS the authoritative source rows themselves: each carries its own
timestamp (characters.created_at, character_jobs.started_at,
organization_members.joined_at/role, crimes.day/status), so a role change is
a source-row change and tests pin those timestamps. The single exception is
the newcomer role's expiry: it is driven purely by WorldClock.game_timestamp
crossing the 7-day boundary — traceable by construction from the clock state,
not from a row write. A separate
mutation-heavy role store would duplicate state and drift; it is rejected
here on purpose.

Determinism: same DB state → same role list, in a fixed order
(newcomer, occupation, org roles by organization id, offender).
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterJob,
    Crime,
    Job,
    Organization,
    OrganizationMember,
    WorldClock,
)

MINUTES_PER_DAY = 1440
NEWCOMER_DAYS = 7

# Seeded job titles (config/default.yaml → seed_world) → Russian role titles.
# Unknown titles fall back to the raw authoritative Job.title.
_OCCUPATION_TITLES_RU = {
    "Farmer": "Фермер",
    "Fisher": "Рыболов",
    "Crafter": "Крафтер",
    "Storekeeper": "Кладовщик",
}

# Roles with no authoritative source in the current schema — never derived.
EXCLUDED_ROLES: Dict[str, str] = {
    "partner": (
        "нет авторитетного источника (нет модели брака/партнёрства); "
        "Relationship.romantic_interest — скаляр настроения, а не факт "
        "партнёрства: неподдерживаемая/размытая роль не изобретается"
    ),
}


def _world_now(session: Session, world_id: str) -> int:
    clock = session.query(WorldClock).filter_by(world_id=world_id).first()
    return int(clock.game_timestamp) if clock is not None else 0


def _newcomer_role(char: Character, now_ts: int) -> Dict[str, Any]:
    days = max(0, (now_ts - int(char.created_at or 0))) // MINUTES_PER_DAY
    return {
        "id": "newcomer",
        "title": "Новичок",
        "source": "недавно на острове",
        "reason": f"в мире {days} дн. (порог {NEWCOMER_DAYS} дн.)",
    }


def _occupation_role(session: Session, char: Character) -> Dict[str, Any] | None:
    job = (
        session.query(Job)
        .join(CharacterJob, CharacterJob.job_id == Job.id)
        .filter(CharacterJob.character_id == char.id)
        .order_by(CharacterJob.id)
        .first()
    )
    started_at = None
    if job is None:
        if char.occupation_id is None:
            return None
        job = session.get(Job, char.occupation_id)
        if job is None:
            return None
    else:
        cj = (
            session.query(CharacterJob)
            .filter_by(character_id=char.id, job_id=job.id)
            .order_by(CharacterJob.id)
            .first()
        )
        started_at = cj.started_at if cj is not None else None
    if started_at is not None:
        reason = "работа подтверждена записью о найме"
        reason += f" с дня {int(started_at) // MINUTES_PER_DAY}"
    else:
        reason = "указана профессия"
    return {
        "id": "occupation",
        "title": _OCCUPATION_TITLES_RU.get(job.title, job.title),
        "source": f"работа: {job.title}",
        "reason": reason,
    }


def _org_roles(session: Session, char: Character) -> List[Dict[str, Any]]:
    rows = (
        session.query(OrganizationMember, Organization)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .filter(OrganizationMember.character_id == char.id)
        .order_by(Organization.id)
        .all()
    )
    roles: List[Dict[str, Any]] = []
    for member, org in rows:
        if member.role == "leader":
            roles.append({
                "id": "org_leader",
                "title": f"Глава «{org.name}»",
                "source": f"организация «{org.name}»",
                "reason": f"роль leader в организации "
                          f"(в составе с дня {int(member.joined_at) // MINUTES_PER_DAY})",
            })
        else:
            roles.append({
                "id": "org_member",
                "title": f"Участник «{org.name}»",
                "source": f"организация «{org.name}»",
                "reason": f"роль member в организации "
                          f"(в составе с дня {int(member.joined_at) // MINUTES_PER_DAY})",
            })
    return roles


def _offender_role(session: Session, world_id: str, char_id: str) -> Dict[str, Any] | None:
    # Only PUBLICLY known offenses grant a public role: unreported crimes are
    # secret facts (no false positive from what nobody knows).
    crime = (
        session.query(Crime)
        .filter(
            Crime.world_id == world_id,
            Crime.actor_character_id == char_id,
            Crime.status.in_(["reported", "resolved"]),
        )
        .order_by(Crime.id)
        .first()
    )
    if crime is None:
        return None
    return {
        "id": "offender",
        "title": "Нарушитель",
        "source": "зарегистрированное нарушение",
        "reason": f"{crime.crime_type} (день {crime.day}, статус {crime.status})",
    }


def derive_roles(session: Session, world_id: str, character_id: str) -> List[Dict[str, Any]]:
    """Public roles of a character, derived on read from authoritative rows.

    Pure read: no writes, no LLM. Same state → same list (fixed order).
    """
    char = session.get(Character, character_id)
    if char is None or char.world_id != world_id:
        return []
    now_ts = _world_now(session, world_id)
    roles: List[Dict[str, Any]] = []
    days = max(0, (now_ts - int(char.created_at or 0))) // MINUTES_PER_DAY
    if days < NEWCOMER_DAYS:
        roles.append(_newcomer_role(char, now_ts))
    occ = _occupation_role(session, char)
    if occ is not None:
        roles.append(occ)
    roles.extend(_org_roles(session, char))
    off = _offender_role(session, world_id, character_id)
    if off is not None:
        roles.append(off)
    return roles
