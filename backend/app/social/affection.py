"""#183: the SOCIALIZE affection rule, extracted for reuse.

One relationship delta per «encounter» — the exact scalar rule the M2
SOCIALIZE completion has always applied (lifecycle.py), now shared by the
#183 HTTP dialogue encounter so both paths apply byte-identical math.

Scope of this helper (deliberately narrow, #183 v1):

- canonical pair ``sorted([a, b])`` + lazy row creation (initial affection
  from config, ``updated_at=game_timestamp`` — same fields lifecycle.py
  writes);
- affection BEFORE >= -30 → normal path delta +2.0; else conflict path
  delta -5.0; the write is clamped to [-100, 100];
- ``rel.updated_at = game_timestamp``;
- returns the RULE delta (+2.0/-5.0 by path — even when the clamp
  reduced the actual write; the honest post-clamp change is computed
  by the dialogue layer for its response field).

NOT here (stays in lifecycle.py's SOCIALIZE branch): stress, the CONFLICT
WorldEvent, the org no_conflict law, the LLM decide enqueue. This helper
is affection-only by construction — no other Relationship scalar is read
or written, no events are emitted, nothing is enqueued.

Return semantics (pinned by the social_event_integrity invariant): the
RULE delta (+2.0 / -5.0 by path), even when the clamp reduced the actual
write — the SOCIAL_INTERACTION payload contract admits exactly [2.0,
-5.0]. The #183 dialogue layer computes the honest post-clamp change for
its response field separately (see apply_dialogue_encounter).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Relationship


def apply_socialize_affection(
    session: Session,
    world_id: str,
    char_a_id: str,
    char_b_id: str,
    game_timestamp: int,
    settings: Settings,
) -> float:
    """Apply the shared SOCIALIZE affection rule to the canonical pair.

    Creates the Relationship row lazily (affection =
    ``settings.social.initial_affection``, ``updated_at=game_timestamp``),
    applies the path rule (+2.0 normal / -5.0 conflict, decided by the
    FRESH row value), clamps the write to [-100, 100], writes
    ``affection`` and ``updated_at``, and returns the RULE delta
    (+2.0 / -5.0 — even when the clamp reduced the actual write; the
    social_event_integrity invariant pins the SOCIAL_INTERACTION payload
    to exactly these values).
    """
    a, b = sorted([char_a_id, char_b_id])
    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=a, character_b=b)
        .first()
    )

    if not rel:
        rel = Relationship(
            world_id=world_id, character_a=a, character_b=b,
            affection=settings.social.initial_affection, updated_at=game_timestamp
        )
        session.add(rel)
        session.flush()

    # Path decided by the FRESH value (stale params ignored — lifecycle
    # contract kept).
    affection_before = rel.affection
    if affection_before >= -30:
        rel.affection = max(-100.0, min(100.0, rel.affection + 2.0))
        delta = 2.0
    else:
        # Conflict path: the -5.0 mutation only. Stress / CONFLICT event /
        # org law / LLM enqueue are SOCIALIZE-branch side effects and stay
        # in lifecycle.py.
        rel.affection = max(-100.0, min(100.0, rel.affection - 5.0))
        delta = -5.0

    rel.updated_at = game_timestamp
    return delta
