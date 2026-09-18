"""M7 (SPEC §38/§106): seed the external Vladivostok — locations, services, contacts.

Content-only: emits no world events (П2 keystone). Deterministic via rng.
"""
from __future__ import annotations

import random

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterProfile,
    ExternalContact,
    ExternalLocation,
    ExternalService,
)


def seed_external_world(session: Session, settings, world_id: str, rng: random.Random) -> None:
    """Catalog from config; NPC contacts from biography (birthplace off-island)."""
    cfg = settings.external
    if not cfg.enabled:
        return

    location_ids: list[int] = []
    city_locations: list[int] = []
    for spec in cfg.locations:
        ext_loc = ExternalLocation(
            world_id=world_id,
            name=spec.name,
            ext_type=spec.ext_type,
            description=spec.description or None,
        )
        session.add(ext_loc)
        session.flush()
        location_ids.append(ext_loc.id)
        city_locations.append(ext_loc.id)
        for svc in spec.services:
            if svc.service_type == "purchase" and not svc.item_type:
                raise ValueError(
                    f"external purchase service at {spec.name!r} has no item_type"
                )
            session.add(ExternalService(
                world_id=world_id,
                external_location_id=ext_loc.id,
                service_type=svc.service_type,
                item_type=svc.item_type,
                price=svc.price,
                heal_amount=svc.heal_amount or None,
                duration_minutes=svc.duration_minutes,
            ))

    # Biographical connections (§106): NPCs come from Vladivostok by default
    # (population.birthplace), so every NPC may keep contacts in the city.
    first_names = list(settings.population.first_names)
    last_names = list(settings.population.last_names)
    contact_types = ["family", "friend", "colleague", "official"]

    npcs = (
        session.query(Character)
        .filter(Character.world_id == world_id, Character.user_id.is_(None))
        .order_by(Character.id)
        .all()
    )
    for npc in npcs:
        profile = session.get(CharacterProfile, npc.id)
        birthplace = (profile.birthplace if profile else None) or settings.population.birthplace
        island_marker = "island"
        if island_marker in birthplace.lower():
            continue  # born on the island: no external contacts
        count = 1 + (1 if rng.random() < cfg.contact_probability else 0)
        for i in range(count):
            session.add(ExternalContact(
                world_id=world_id,
                character_id=npc.id,
                contact_type=contact_types[i % 2],  # family, then friend
                name=f"{rng.choice(first_names)} {rng.choice(last_names)}",
                external_location_id=rng.choice(city_locations),
                note=f"contact from {birthplace}",
            ))
    session.flush()
