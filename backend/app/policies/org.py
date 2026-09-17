from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterNeeds,
    CharacterTrait,
    Organization,
    OrganizationMember,
    OrgLaw,
    OrgLawViolation,
    Relationship,
    RelationshipEvent,
)
from app.economy import burn, get_balance, open_account, transfer
from app.events.events import EventType, log_event


def _get_band(aff: float) -> str:
    """Helper to determine relationship band from affection."""
    if aff < -30:
        return "conflicted"
    if aff < 10:
        return "stranger"
    if aff < 50:
        return "acquaintance"
    return "friend"

def _enact_traits_of(entry) -> dict:
    """Catalog entries are dicts in tests and LawEntry models from config."""
    if isinstance(entry, dict):
        return entry.get("enact_traits", {}) or {}
    return dict(getattr(entry, "enact_traits", {}) or {})

def choose_law(leader_traits: dict[str, int], catalog: dict) -> str:
    """
    Selects a law from the catalog by maximizing the dot product of leader traits
    and the law's enactment traits.
    """
    best_law = None
    max_score = float('-inf')

    trait_keys = ["sociability", "discipline", "risk_tolerance"]

    for law_key, entry in catalog.items():
        enact_traits = _enact_traits_of(entry)
        score = 0
        for k in trait_keys:
            score += leader_traits.get(k, 0) * enact_traits.get(k, 0)

        if score > max_score:
            max_score = score
            best_law = law_key

    return best_law

def _law_fine_of(catalog, law_key: str):
    """Catalog values are LawEntry models from Settings or dicts in tests."""
    entry = catalog.get(law_key) if hasattr(catalog, "get") else None
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("fine")
    return getattr(entry, "fine", None)

def enforce_no_conflict(session: Session, world_id: str, settings, game_timestamp: int,
                        source_event_id, participant_ids) -> None:
    """M3 R4: fine CONFLICT participants when the community's active law is
    no_conflict. R1 gate first — zero DB access when disabled (byte-identity).
    Once per character per day: pre-check before any money moves; the DB
    UNIQUE (org, law, char, day) is the ultimate guard (R8)."""
    if not (settings.org.enabled and settings.social.enabled
            and settings.org.laws.enabled):
        return

    community_org = session.query(Organization).filter_by(
        world_id=world_id, type="community").first()
    if not community_org:
        return

    law = session.query(OrgLaw).filter_by(
        world_id=world_id, organization_id=community_org.id).first()
    if not law or law.law_key != "no_conflict":
        return

    fine_amount = _law_fine_of(settings.org.laws.catalog, "no_conflict")
    if fine_amount is None:
        fine_amount = 0

    game_day = game_timestamp // 1440
    community_acc = open_account(session, world_id, "organization", str(community_org.id))

    for character_id in participant_ids:
        existing = session.query(OrgLawViolation).filter_by(
            world_id=world_id, organization_id=community_org.id,
            law_key="no_conflict", character_id=character_id,
            game_day=game_day
        ).first()
        if existing:
            continue  # one fine per character per day (R4/R8)

        fine_paid = 0
        try:
            char_acc = open_account(session, world_id, "character", character_id)
            transfer(
                session=session,
                world_id=world_id,
                game_timestamp=game_timestamp,
                from_account_id=char_acc.id,
                to_account_id=community_acc.id,
                amount=fine_amount,
                reason="LAW_FINE"
            )
            fine_paid = fine_amount
        except ValueError:
            pass  # silent skip when unpayable (fine_paid=0 in the row)

        session.add(OrgLawViolation(
            world_id=world_id,
            organization_id=community_org.id,
            law_key="no_conflict",
            character_id=character_id,
            game_day=game_day,
            game_timestamp=game_timestamp,
            fine_paid=fine_paid,
            event_id=source_event_id
        ))

        log_event(
            session, world_id, game_timestamp, EventType.LAW_VIOLATION,
            actor_id=character_id,
            payload={
                "organization_id": community_org.id,
                "law_key": "no_conflict",
                "fine": fine_amount,
                "fine_paid": fine_paid,
                "source_event_type": "CONFLICT"
            }
        )

def enact_initial_laws(session: Session, world_id: str, settings, timestamp: int = 0):
    """
    Enacts the initial law for the community organization on day 0.
    Gated by settings.org.enabled, settings.org.laws.enabled, and settings.social.enabled.
    """
    if not (settings.org.enabled and settings.org.laws.enabled and settings.social.enabled):
        return

    # Find the community organization
    community_org = session.query(Organization).filter_by(
        world_id=world_id, type="community").first()
    if not community_org:
        return

    # Idempotency: check if a law already exists for this organization
    existing_law = session.query(OrgLaw).filter_by(
        world_id=world_id, organization_id=community_org.id).first()
    if existing_law:
        return

    # Find the current leader of the community organization
    leader_member = session.query(OrganizationMember).filter_by(
        organization_id=community_org.id, role="leader"
    ).first()

    if not leader_member:
        return

    # Get leader's traits
    trait_rows = session.query(CharacterTrait).filter_by(
        character_id=leader_member.character_id).all()
    traits = {t.trait_key: t.value for t in trait_rows}

    # Choose law based on traits
    law_key = choose_law(traits, settings.org.laws.catalog)
    if not law_key:
        return

    # Insert OrgLaw
    new_law = OrgLaw(
        world_id=world_id,
        organization_id=community_org.id,
        law_key=law_key,
        enacted_by_character_id=leader_member.character_id,
        enacted_day=0,
        enacted_at=timestamp
    )
    session.add(new_law)
    session.flush()

    # Log the event
    log_event(
        session=session,
        world_id=world_id,
        game_timestamp=timestamp,
        event_type=EventType.LAW_ENACTED,
        actor_id=leader_member.character_id,
        payload={
            "organization_id": community_org.id,
            "law_key": law_key,
            "replaced_law_key": None
        }
    )
    return

def run_daily_org_policies(session: Session, world_id: str, settings, game_timestamp: int):
    """
    Daily organization policies handler (M3 Chunk B).
    Order: Dues -> Elections -> Law Enactment -> Night Home -> Reconciliation -> Feast.
    """
    if not (settings.org.enabled and settings.social.enabled):
        return

    game_day = game_timestamp // 1440

    # Process organizations by ID ascending
    organizations = (
        session.query(Organization)
        .filter_by(world_id=world_id)
        .order_by(Organization.id)
        .all()
    )

    for org in organizations:
        # --- 0. Alive members (R2/R3: org without alive members -> no dues,
        # no elections, no law step; feast checks its own count below) ---
        members = (
            session.query(OrganizationMember)
            .filter_by(organization_id=org.id)
            .join(Character)
            .filter_by(alive=True)
            .order_by(OrganizationMember.character_id)
            .all()
        )
        alive_members = [m.character_id for m in members]
        if not alive_members:
            continue

        # --- 1. Dues ---
        dues_per_day = settings.org.dues_per_day
        paid_members = 0
        skipped_members = 0
        total_collected = 0

        org_acc = open_account(session, world_id, "organization", str(org.id))

        for member in members:
            char_acc = open_account(session, world_id, "character", member.character_id)
            try:
                transfer(
                    session=session,
                    world_id=world_id,
                    game_timestamp=game_timestamp,
                    from_account_id=char_acc.id,
                    to_account_id=org_acc.id,
                    amount=dues_per_day,
                    reason="ORG_DUES"
                )
                paid_members += 1
                total_collected += dues_per_day
            except ValueError:
                skipped_members += 1

        log_event(
            session, world_id, game_timestamp, EventType.ORG_DUES,
            actor_id=None,
            payload={
                "organization_id": org.id,
                "amount_per_member": dues_per_day,
                "paid_members": paid_members,
                "skipped_members": skipped_members,
                "total_collected": total_collected
            }
        )

        # --- 2. Elections ---
        election_happened = False
        winner_id = None
        prev_leader_id = None
        election_reason = None

        # Check for succession or scheduled election
        leader_member = session.query(OrganizationMember).filter_by(
            organization_id=org.id, role="leader"
        ).first()

        prev_leader_id = leader_member.character_id if leader_member else None
        leader_char = (
            session.query(Character).filter_by(id=prev_leader_id).first()
            if prev_leader_id else None
        )

        is_scheduled_day = (game_day > 0 and game_day % settings.org.election_interval_days == 0)
        needs_succession = (
            leader_char is None
            or not leader_char.alive
            or leader_char.death_game_timestamp is not None
        )
        # NOTE: a missing leader row (unreachable via seed/elections, but
        # defensive) also triggers an election on non-scheduled days — this
        # restores exactly-one-alive-leader per org (invariant R9.1) instead
        # of leaving the org leaderless.

        if is_scheduled_day:
            election_reason = "scheduled"
            election_happened = True
        elif needs_succession:
            election_reason = "succession"
            election_happened = True

        if election_happened:
            # Calculate scores: score(c) = Σ affection(v, c) over alive voters v != c
            candidates = alive_members
            scores = {}
            for c_id in candidates:
                score = 0.0
                for v_id in alive_members:
                    if v_id == c_id:
                        continue
                    a, b = sorted([v_id, c_id])
                    rel = session.query(Relationship).filter_by(
                        world_id=world_id, character_a=a, character_b=b
                    ).first()
                    score += rel.affection if rel else 0.0
                scores[c_id] = score

            # Winner = max score, tie -> lowest character_id
            winner_id = min(candidates, key=lambda cid: (-scores[cid], cid))

            # Effect: flip roles
            if prev_leader_id:
                leader_member.role = "member"

            # Find or create winner's member row
            winner_member = session.query(OrganizationMember).filter_by(
                organization_id=org.id, character_id=winner_id
            ).first()
            if winner_member:
                winner_member.role = "leader"
            else:
                # Should not happen if winner is from alive_members
                winner_member = OrganizationMember(
                    organization_id=org.id, character_id=winner_id,
                    role="leader", joined_at=game_timestamp
                )
                session.add(winner_member)

            # Update Organization leader_character_id
            org.leader_character_id = winner_id

            log_event(
                session, world_id, game_timestamp, EventType.ELECTION,
                actor_id=winner_id,
                target_id=prev_leader_id,
                payload={
                    "organization_id": org.id,
                    "previous_leader_id": prev_leader_id,
                    "score": scores[winner_id],
                    "candidates_count": len(candidates),
                    "reason": election_reason
                }
            )
            session.flush()

        # --- 3. Law Enactment ---
        if (election_happened and winner_id != prev_leader_id
                and settings.org.laws.enabled):
            # Get winner's traits
            trait_rows = session.query(CharacterTrait).filter_by(
                character_id=winner_id
            ).all()
            winner_traits = {t.trait_key: t.value for t in trait_rows}

            new_law_key = choose_law(winner_traits, settings.org.laws.catalog)
            if new_law_key:
                existing_law = session.query(OrgLaw).filter_by(
                    world_id=world_id, organization_id=org.id
                ).first()

                if not existing_law or existing_law.law_key != new_law_key:
                    old_law_key = existing_law.law_key if existing_law else None
                    if existing_law:
                        existing_law.law_key = new_law_key
                        existing_law.enacted_by_character_id = winner_id
                        existing_law.enacted_day = game_day
                        existing_law.enacted_at = game_timestamp
                    else:
                        new_law = OrgLaw(
                            world_id=world_id,
                            organization_id=org.id,
                            law_key=new_law_key,
                            enacted_by_character_id=winner_id,
                            enacted_day=game_day,
                            enacted_at=game_timestamp
                        )
                        session.add(new_law)

                    log_event(
                        session, world_id, game_timestamp, EventType.LAW_ENACTED,
                        actor_id=winner_id,
                        payload={
                            "organization_id": org.id,
                            "law_key": new_law_key,
                            "replaced_law_key": old_law_key
                        }
                    )
                    session.flush()

    # --- 4. Night Home Check (Community Org only) ---
    if settings.org.laws.enabled:
        community_org = session.query(Organization).filter_by(
            world_id=world_id, type="community"
        ).first()
        if community_org:
            law = session.query(OrgLaw).filter_by(
                world_id=world_id, organization_id=community_org.id
            ).first()
            if law and law.law_key == "night_home":
                fine_amount = _law_fine_of(settings.org.laws.catalog, "night_home")
                if fine_amount is None:
                    fine_amount = 0
                community_acc = open_account(
                    session, world_id, "organization", str(community_org.id))

                # For each alive community member
                community_members = (
                    session.query(OrganizationMember)
                    .filter_by(organization_id=community_org.id)
                    .join(Character)
                    .filter_by(alive=True)
                    .all()
                )

                for member in community_members:
                    char = session.query(Character).filter_by(id=member.character_id).first()
                    if char is None:
                        continue
                    if (char.home_location_id is not None
                            and char.location_id != char.home_location_id):
                        # Once-per-day idempotency (R8: UNIQUE lives in the DB):
                        # pre-check BEFORE any money moves to avoid a fine leak.
                        existing = session.query(OrgLawViolation).filter_by(
                            world_id=world_id, organization_id=community_org.id,
                            law_key="night_home", character_id=char.id,
                            game_day=game_day
                        ).first()
                        if existing:
                            continue

                        fine_paid = 0
                        try:
                            char_acc = open_account(session, world_id, "character", char.id)
                            transfer(
                                session=session,
                                world_id=world_id,
                                game_timestamp=game_timestamp,
                                from_account_id=char_acc.id,
                                to_account_id=community_acc.id,
                                amount=fine_amount,
                                reason="LAW_FINE"
                            )
                            fine_paid = fine_amount
                        except ValueError:
                            pass  # silent skip when unpayable

                        session.add(OrgLawViolation(
                            world_id=world_id,
                            organization_id=community_org.id,
                            law_key="night_home",
                            character_id=char.id,
                            game_day=game_day,
                            game_timestamp=game_timestamp,
                            fine_paid=fine_paid,
                            event_id=None
                        ))
                        session.flush()

                        log_event(
                            session, world_id, game_timestamp, EventType.LAW_VIOLATION,
                            actor_id=char.id,
                            payload={
                                "organization_id": community_org.id,
                                "law_key": "night_home",
                                "fine": fine_amount,
                                "fine_paid": fine_paid,
                                "source_event_type": None
                            }
                        )

    # --- 5. Reconciliation (Community Org only) ---
    if settings.org.reconciliation.enabled:
        community_org = session.query(Organization).filter_by(
            world_id=world_id, type="community"
        ).first()
        if community_org:
            # Check leader status
            leader_member = session.query(OrganizationMember).filter_by(
                organization_id=community_org.id, role="leader"
            ).first()
            if leader_member:
                leader_char = session.query(Character).filter_by(
                    id=leader_member.character_id).first()
                if leader_char and leader_char.alive:
                    # Find first eligible pair: alive, community members, affection <= -60.0
                    community_members = (
                        session.query(OrganizationMember)
                        .filter_by(organization_id=community_org.id)
                        .join(Character)
                        .filter_by(alive=True)
                        .order_by(OrganizationMember.character_id)
                        .all()
                    )
                    member_ids = [m.character_id for m in community_members]

                    # First eligible pair in (a, b) id-ascending order (R6).
                    eligible = None
                    for i in range(len(member_ids)):
                        for j in range(i + 1, len(member_ids)):
                            char_a_id = member_ids[i]
                            char_b_id = member_ids[j]
                            a, b = sorted([char_a_id, char_b_id])
                            rel = session.query(Relationship).filter_by(
                                world_id=world_id, character_a=a, character_b=b
                            ).first()
                            if rel and rel.affection <= -60.0:
                                eligible = (char_a_id, char_b_id, a, b, rel)
                                break
                        if eligible:
                            break

                    # Leader dead/absent or leader in the pair -> skip the day
                    # (R6: retry tomorrow, no state, no events). No scanning of
                    # further pairs: the FIRST eligible pair owns the day.
                    if eligible and leader_member:
                        char_a_id, char_b_id, a, b, rel = eligible
                        if leader_member.character_id in (char_a_id, char_b_id):
                            eligible = None

                    if eligible:
                        char_a_id, char_b_id, a, b, rel = eligible
                        # Mediator: alive community member m not in {a, b}
                        # maximizing aff(m,a) + aff(m,b); tie -> lowest id.
                        best_mediator_id = None
                        max_mediator_score = float('-inf')

                        for m_id in member_ids:
                            if m_id == char_a_id or m_id == char_b_id:
                                continue
                            # Score = aff(m, a) + aff(m, b); missing pair -> 0.0
                            score = 0.0
                            for other in [char_a_id, char_b_id]:
                                s_a, s_b = sorted([m_id, other])
                                r = session.query(Relationship).filter_by(
                                    world_id=world_id, character_a=s_a, character_b=s_b
                                ).first()
                                score += r.affection if r else 0.0

                            if score > max_mediator_score:
                                max_mediator_score = score
                                best_mediator_id = m_id

                        if best_mediator_id:
                            # Reconciliation happens
                            aff_before = rel.affection
                            aff_after = settings.org.reconciliation.target_affection
                            rel.affection = aff_after
                            rel.updated_at = game_timestamp

                            # RECONCILIATION event
                            log_event(
                                session, world_id, game_timestamp, EventType.RECONCILIATION,
                                actor_id=best_mediator_id,
                                payload={
                                    "organization_id": community_org.id,
                                    "pair": [char_a_id, char_b_id],
                                    "mediator_id": best_mediator_id,
                                    "affection_before": aff_before,
                                    "affection_after": aff_after
                                }
                            )

                            # RELATIONSHIP_CHANGED event (M2 convention: band
                            # crossing only) + relationship_events row
                            band_before = _get_band(aff_before)
                            band_after = _get_band(aff_after)
                            if band_before != band_after:
                                event_id = log_event(
                                    session, world_id, game_timestamp,
                                    EventType.RELATIONSHIP_CHANGED,
                                    actor_id=char_a_id, target_id=char_b_id,
                                    payload={
                                        "band": band_after,
                                        "affection_before": aff_before,
                                        "affection_after": aff_after
                                    }
                                )
                                session.add(RelationshipEvent(
                                    world_id=world_id, character_a=a, character_b=b,
                                    event_type=EventType.RELATIONSHIP_CHANGED.value,
                                    impact=aff_after - aff_before,
                                    event_id=event_id,
                                    game_timestamp=game_timestamp
                                ))

    # --- 6. Feast ---
    if game_day > 0 and game_day % settings.org.feast_interval_days == 0:
        # For each org with >= 1 alive member
        for org in organizations:
            # Check alive members
            alive_members_count = (
                session.query(func.count(OrganizationMember.id))
                .join(Character, OrganizationMember.character_id == Character.id)
                .filter(
                    OrganizationMember.organization_id == org.id,
                    Character.alive.is_(True),
                )
                .scalar()
            )

            if alive_members_count > 0:
                feast_cost = settings.org.feast_cost
                org_acc = open_account(session, world_id, "organization", str(org.id))
                if get_balance(session, org_acc.id) >= feast_cost:
                    # Burn cost
                    burn(
                        session=session,
                        world_id=world_id,
                        game_timestamp=game_timestamp,
                        from_account_id=org_acc.id,
                        amount=feast_cost,
                        reason="ORG_FEAST"
                    )

                    # Boost social for alive members
                    members = (
                        session.query(OrganizationMember)
                        .filter_by(organization_id=org.id)
                        .join(Character)
                        .filter_by(alive=True)
                        .all()
                    )

                    boost = settings.org.feast_social_boost
                    boosted_members = []
                    for m in members:
                        needs = session.query(CharacterNeeds).filter_by(
                            character_id=m.character_id).one()
                        needs.social = min(100.0, needs.social + boost)
                        boosted_members.append(m.character_id)

                    # Find current alive leader for actor_id
                    leader_member = session.query(OrganizationMember).filter_by(
                        organization_id=org.id, role="leader"
                    ).first()
                    actor_id = None
                    if leader_member:
                        l_char = session.query(Character).filter_by(
                            id=leader_member.character_id).first()
                        if l_char and l_char.alive:
                            actor_id = l_char.id

                    log_event(
                        session, world_id, game_timestamp, EventType.ORG_FEAST,
                        actor_id=actor_id,
                        payload={
                            "organization_id": org.id,
                            "cost": feast_cost,
                            "boosted_members": boosted_members
                        }
                    )
                    session.flush()

    session.flush()
