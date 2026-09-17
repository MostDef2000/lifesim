"""M2 social seeding: organization membership, leaders, seeded conflicts.

Runs AFTER generate_population (characters must exist). Deterministic: all
pairing/selection derives from the worldgen RNG stream passed by the caller
(M1 R2 — worldgen RNG only) plus sorted-id scans; no runtime RNG.
"""
import random
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterJob,
    Job,
    Organization,
    OrganizationMember,
    Relationship,
)


def seed_social(session: Session, settings, world_id: str, rng: random.Random) -> None:
    """Seed org membership + leaders and initial conflict relationships.

    Called once per worldgen, after generate_population. Emits no events
    (seed-time state, not runtime). No-op when social.enabled is false:
    M2 must leave social-off runs byte-identical to M1 (no rows, no RNG
    consumption).
    """
    if not settings.social.enabled:
        return
    all_chars = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .order_by(Character.id)
        .all()
    )
    char_ids = [c.id for c in all_chars]

    jobs_by_id = {j.id: j for j in session.query(Job).all()}
    char_to_org: Dict[str, int] = {}
    job_title_by_char: Dict[str, str] = {}
    for cj in session.query(CharacterJob).all():
        job = jobs_by_id.get(cj.job_id)
        if job is None:
            continue
        char_to_org[cj.character_id] = job.organization_id
        job_title_by_char[cj.character_id] = job.title

    orgs = (
        session.query(Organization)
        .filter_by(world_id=world_id)
        .order_by(Organization.id)
        .all()
    )
    org_by_type = {}
    for org in orgs:
        org_by_type.setdefault(org.type, org.id)

    # leader_titles live in config per organization (matched by name).
    leader_titles_by_org_name: Dict[str, List[str]] = {
        params["name"]: list(params.get("leader_titles", []) or [])
        for params in settings.economy.organizations.values()
    }

    # 1) Everyone belongs to the community org.
    community_org_id = org_by_type.get("community")
    if community_org_id is not None:
        for cid in char_ids:
            session.add(OrganizationMember(
                organization_id=community_org_id,
                character_id=cid,
                role="member",
                joined_at=0,
            ))

    # 2) Job holders belong to their employer org (skip community double-add).
    for cid, org_id in char_to_org.items():
        if org_id != community_org_id:
            session.add(OrganizationMember(
                organization_id=org_id,
                character_id=cid,
                role="member",
                joined_at=0,
            ))

    # 3) Leaders: lowest-id character whose job title is in the org's
    # leader_titles (config), fallback lowest-id character overall.
    # Exactly one leader per org.
    for org in orgs:
        leader_titles = leader_titles_by_org_name.get(org.name, [])
        leader_id = None
        for cid in char_ids:
            if job_title_by_char.get(cid) in leader_titles:
                leader_id = cid
                break
        if leader_id is None and char_ids:
            leader_id = char_ids[0]
        if leader_id is None:
            continue
        member = (
            session.query(OrganizationMember)
            .filter_by(organization_id=org.id, character_id=leader_id)
            .first()
        )
        if member is not None:
            member.role = "leader"
        else:
            session.add(OrganizationMember(
                organization_id=org.id,
                character_id=leader_id,
                role="leader",
                joined_at=0,
            ))

    # 4) Seeded old conflicts (SPEC §98): initial_conflicts pairs at
    # affection = -50, canonical order character_a < character_b.
    # Deduplicated retry loop: guarantees exactly N distinct pairs when
    # enough characters exist, immune to sample collisions.
    if len(char_ids) >= 2:
        seeded_pairs = 0
        attempts = 0
        max_attempts = settings.social.initial_conflicts * 50
        while seeded_pairs < settings.social.initial_conflicts and attempts < max_attempts:
            attempts += 1
            a, b = sorted(rng.sample(char_ids, 2))
            exists = (
                session.query(Relationship)
                .filter_by(world_id=world_id, character_a=a, character_b=b)
                .first()
            )
            if exists is not None:
                continue
            session.add(Relationship(
                world_id=world_id,
                character_a=a,
                character_b=b,
                affection=-50.0,
                updated_at=0,
            ))
            seeded_pairs += 1

    session.flush()
