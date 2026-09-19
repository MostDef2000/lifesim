"""018 (§31): explicit consent state for romantic interactions.

П1: consent is an explicit DB row, never derived from LLM text.
П4: romance.enabled=false -> API rejects before any row is written.
"""
from sqlalchemy.orm import Session

CATEGORY_ROMANCE = "romance"
PERMISSION_VALUES = ("requested", "granted", "declined")


class ConsentError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _get_row(session: Session, world_id: str, actor_id: str,
             target_id: str):
    from app.db.models import InteractionPermission

    return (
        session.query(InteractionPermission)
        .filter_by(
            world_id=world_id,
            actor_character_id=actor_id,
            target_character_id=target_id,
            interaction_category=CATEGORY_ROMANCE,
        )
        .first()
    )


def request_romantic(session: Session, world_id: str, now_ts: int,
                     actor, target, settings) -> str:
    """Player asks NPC for romance. Deterministic NPC decision (R2).

    affection >= affection_grant_threshold -> granted, else declined.
    Re-request returns the existing decision (no duplicate rows, AE2).
    """
    from app.db.models import InteractionPermission, Relationship

    if not settings.romance.enabled:
        raise ConsentError(403, "romance_disabled")
    if actor.id == target.id:
        raise ConsentError(422, "self_request")
    existing = _get_row(session, world_id, actor.id, target.id)
    if existing is not None:
        return existing.permission
    a, b = sorted([actor.id, target.id])
    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=a, character_b=b)
        .first()
    )
    affection = rel.affection if rel is not None else 0.0
    permission = (
        "granted"
        if affection >= settings.romance.affection_grant_threshold
        else "declined"
    )
    row = InteractionPermission(
        world_id=world_id,
        actor_character_id=actor.id,
        target_character_id=target.id,
        interaction_category=CATEGORY_ROMANCE,
        permission=permission,
        created_at=now_ts,
        updated_at=now_ts,
    )
    session.add(row)
    return permission


def list_permissions(session: Session, world_id: str,
                     character_id: str) -> list:
    """R3: rows where the character is actor OR target."""
    from app.db.models import InteractionPermission

    rows = (
        session.query(InteractionPermission)
        .filter(
            InteractionPermission.world_id == world_id,
            (InteractionPermission.actor_character_id == character_id)
            | (InteractionPermission.target_character_id == character_id),
        )
        .all()
    )
    return [
        {
            "actor_character_id": r.actor_character_id,
            "target_character_id": r.target_character_id,
            "interaction_category": r.interaction_category,
            "permission": r.permission,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]
