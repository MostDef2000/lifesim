"""018 (§28): clothing as WorldObject metadata (MVP).

Slots: head/upper_body/lower_body/feet/underwear/accessory. MVP sells
jacket (upper_body), boots (feet), hat (head) in the shop. Worn state
lives in object_metadata JSON; portrait prompt gains "wearing: [...]"
only when worn items exist (byte-identity without them, m6 pins).
"""
import json

from sqlalchemy.orm import Session

# object_type -> §28 slot
SLOT_MAP = {
    "jacket": "upper_body",
    "boots": "feet",
    "hat": "head",
}
WEARABLE_TYPES = tuple(SLOT_MAP)


class WearError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _metadata(obj) -> dict:
    try:
        data = json.loads(obj.object_metadata or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def worn_items(session: Session, world_id: str, character_id: str) -> list:
    """Worn wearable objects owned by the character, ordered by id."""
    from app.db.models import WorldObject

    rows = (
        session.query(WorldObject)
        .filter(
            WorldObject.world_id == world_id,
            WorldObject.owner_character_id == character_id,
            WorldObject.object_type.in_(WEARABLE_TYPES),
        )
        .order_by(WorldObject.id)
        .all()
    )
    return [r for r in rows if _metadata(r).get("worn")]


def wearing_phrases(session: Session, world_id: str,
                    character_id: str) -> list:
    """'wearing' fragments for the portrait prompt (empty -> no change)."""
    return [
        f"{r.object_type} on {_metadata(r).get('slot')}"
        for r in worn_items(session, world_id, character_id)
    ]


def toggle_wear(session: Session, world_id: str, character_id: str,
                object_id: int) -> dict:
    """R4: toggle worn on own wearable; 404 for foreign/unknown items."""
    from app.db.models import WorldObject

    obj = session.get(WorldObject, object_id)
    if (obj is None or obj.world_id != world_id
            or obj.owner_character_id != character_id):
        raise WearError(404, "not_found")
    if obj.object_type not in SLOT_MAP:
        raise WearError(422, "not_wearable")
    meta = _metadata(obj)
    meta["slot"] = SLOT_MAP[obj.object_type]
    meta["worn"] = not bool(meta.get("worn"))
    obj.object_metadata = json.dumps(meta, sort_keys=True)
    return {"object_id": obj.id, "slot": meta["slot"],
            "worn": meta["worn"]}
