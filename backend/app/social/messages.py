"""Messages (013): letters/phone — contacts as a communication channel."""

from sqlalchemy.orm import Session

from app.db.models import Character, Message, Relationship
from app.events.events import EventType, log_event


class MessageError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        super().__init__(code)


_MAX_BODY = 2000

_FALLBACK_REPLIES = [
    # (affection >= threshold, template)
    (50.0, "Привет, {player}! Получил твоё письмо — рад был услышать."),
    (0.0, "Привет, {player}. Письмо получил, спасибо."),
    (-50.0, "{player}, письмо получил. Не буду отвечать подробно."),
]


def send_message(
    session: Session, world_id: str, game_timestamp: int,
    sender: Character, to_character_id: str, body: str,
) -> Message:
    """Player -> NPC letter. Guard: existing relationship or co-location.
    NPC replies deterministically immediately (MVP simplification)."""
    body = (body or "").strip()
    if not body or len(body) > _MAX_BODY:
        raise MessageError("bad_body")
    recipient = (
        session.query(Character)
        .filter_by(id=to_character_id, world_id=world_id)
        .first()
    )
    if recipient is None or not recipient.alive:
        raise MessageError("recipient_not_found", 404)
    if recipient.id == sender.id:
        raise MessageError("self_message", 409)

    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=sender.id, character_b=recipient.id)
        .first()
    )
    if rel is None:
        rel = (
            session.query(Relationship)
            .filter_by(world_id=world_id, character_a=recipient.id, character_b=sender.id)
            .first()
        )
    if rel is None and recipient.location_id != sender.location_id:
        raise MessageError("no_contact", 403)

    msg = Message(
        world_id=world_id, from_character_id=sender.id,
        to_character_id=recipient.id, body=body,
        created_at=game_timestamp,
    )
    session.add(msg)
    session.flush()
    log_event(
        session, world_id, game_timestamp, EventType.MESSAGE_SENT,
        actor_id=sender.id,
        payload={"to": recipient.id, "len": len(body)},
    )

    # deterministic NPC auto-reply (immediate — MVP simplification, spec)
    affection = rel.affection if rel is not None else 0.0
    player_name = f"{sender.first_name} {sender.last_name}".strip()
    template = _FALLBACK_REPLIES[1][1]
    for threshold, tpl in _FALLBACK_REPLIES:
        if affection >= threshold:
            template = tpl
            break
    reply = Message(
        world_id=world_id, from_character_id=recipient.id,
        to_character_id=sender.id, body=template.format(player=player_name),
        created_at=game_timestamp,
    )
    session.add(reply)
    session.flush()
    log_event(
        session, world_id, game_timestamp, EventType.MESSAGE_SENT,
        actor_id=recipient.id,
        payload={"to": sender.id, "len": len(reply.body), "auto": True},
    )
    return msg


def inbox(session: Session, world_id: str, char: Character) -> list[Message]:
    return (
        session.query(Message)
        .filter_by(world_id=world_id, to_character_id=char.id)
        .order_by(Message.id.desc())
        .limit(100)
        .all()
    )


def sent(session: Session, world_id: str, char: Character) -> list[Message]:
    return (
        session.query(Message)
        .filter_by(world_id=world_id, from_character_id=char.id)
        .order_by(Message.id.desc())
        .limit(100)
        .all()
    )


def mark_read(session: Session, world_id: str, char: Character) -> int:
    unread = (
        session.query(Message)
        .filter_by(world_id=world_id, to_character_id=char.id, read_at=None)
        .all()
    )
    from app.db.models import WorldClock

    clock = session.query(WorldClock).filter_by(world_id=world_id).first()
    ts = clock.game_timestamp if clock is not None else 0
    for m in unread:
        m.read_at = ts
    session.flush()
    return len(unread)
