import json
from typing import Any, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Character, World, WorldClock, WorldEvent
from app.simulation.invariants import run_invariant_checks


def build_report(
    session: Session, world_id: str, settings,
    wall_duration_sec: float, seed: int
) -> Dict[str, Any]:
    """
    Builds the final JSON report for the simulation run.
    """
    # World metadata
    world_row = session.query(World).filter(World.id == world_id).first()
    config_sha256 = world_row.config_sha256 if world_row else "unknown"

    # Clock state
    clock_row = session.query(WorldClock).filter(WorldClock.world_id == world_id).first()
    final_timestamp = clock_row.game_timestamp if clock_row else 0
    final_day = final_timestamp // 1440

    # Population metrics
    total_pop = (
        session.query(func.count(Character.id))
        .filter(Character.world_id == world_id)
        .scalar() or 0
    )
    alive_pop = (
        session.query(func.count(Character.id))
        .filter(Character.world_id == world_id, Character.alive.is_(True))
        .scalar() or 0
    )
    dead_pop = total_pop - alive_pop

    # Event distribution
    event_counts = session.query(WorldEvent.event_type, func.count(WorldEvent.id)).filter(
        WorldEvent.world_id == world_id
    ).group_by(WorldEvent.event_type).all()
    events_by_type = {etype: count for etype, count in event_counts}

    # Invariants
    invariant_results = run_invariant_checks(session, world_id, settings)
    invariants_ok = all(res["ok"] for res in invariant_results)

    report = {
        "config_sha256": config_sha256,
        "seed": seed,
        "wall_duration_sec": wall_duration_sec,
        "final_day": final_day,
        "population_total": total_pop,
        "population_alive": alive_pop,
        "dead_count": dead_pop,
        "events_by_type": events_by_type,
        "invariant_results": invariant_results,
        "invariants_ok": invariants_ok
    }

    if settings.social.enabled:
        from app.db.models import Organization, OrganizationMember, Relationship
        orgs = session.query(Organization).filter(Organization.world_id == world_id).all()
        org_list = []
        social_interactions = events_by_type.get("SOCIAL_INTERACTION", 0)
        conflicts = events_by_type.get("CONFLICT", 0)
        rel_count = session.query(func.count(Relationship.id)).filter(
            Relationship.world_id == world_id
        ).scalar() or 0

        for o in orgs:
            member_count = session.query(OrganizationMember).filter(
                OrganizationMember.organization_id == o.id
            ).count()
            # Leader per spec R6: OrganizationMember with role='leader'
            # (Organization.leader_character_id is a legacy nullable column,
            # not populated by seed_social).
            leader = session.query(OrganizationMember).filter(
                OrganizationMember.organization_id == o.id,
                OrganizationMember.role == "leader"
            ).first()
            org_list.append({
                "name": o.name,
                "member_count": member_count,
                "leader_id": leader.character_id if leader else None
            })
        report["social"] = {
            "organizations": org_list,
            "summary": {
                "relationship_count": rel_count,
                "social_interactions": social_interactions,
                "conflicts": conflicts
            }
        }

    # M3 R10: org block — only when the org mechanics are active (R1 gate).
    if settings.org.enabled and settings.social.enabled:
        from app.db.models import (
            Account,
            Organization,
            OrganizationMember,
            OrgLaw,
        )
        orgs = (
            session.query(Organization)
            .filter(Organization.world_id == world_id)
            .order_by(Organization.id)
            .all()
        )
        org_entries = []
        sum_elections = sum_violations = 0
        fines_total = fines_unpaid = sum_feasts = sum_reconciliations = 0

        for o in orgs:
            member_count = (
                session.query(func.count(OrganizationMember.id))
                .filter(OrganizationMember.organization_id == o.id)
                .scalar() or 0
            )
            leader = (
                session.query(OrganizationMember)
                .filter(
                    OrganizationMember.organization_id == o.id,
                    OrganizationMember.role == "leader",
                )
                .first()
            )
            acc = (
                session.query(Account)
                .filter_by(
                    world_id=world_id,
                    owner_type="organization",
                    owner_id=str(o.id),
                )
                .first()
            )
            treasury_balance = acc.balance if acc else 0
            law_row = (
                session.query(OrgLaw)
                .filter_by(world_id=world_id, organization_id=o.id)
                .first()
            )

            dues_collected_total = 0
            feasts = 0
            violations = 0
            org_fines_total = 0
            org_fines_unpaid = 0
            for ev in session.query(WorldEvent).filter_by(
                    world_id=world_id).order_by(WorldEvent.id).all():
                if ev.payload is None:
                    continue
                payload = json.loads(ev.payload)
                if payload.get("organization_id") != o.id:
                    continue
                if ev.event_type == "ORG_DUES":
                    dues_collected_total += payload.get("total_collected", 0)
                elif ev.event_type == "ORG_FEAST":
                    feasts += 1
                elif ev.event_type == "LAW_VIOLATION":
                    violations += 1
                    org_fines_total += payload.get("fine", 0)
                    org_fines_unpaid += (
                        payload.get("fine", 0) - payload.get("fine_paid", 0))
                elif ev.event_type == "RECONCILIATION":
                    sum_reconciliations += 1

            elections = []
            leader_since_day = 0
            leader_id = leader.character_id if leader else None
            for ev in session.query(WorldEvent).filter_by(
                    world_id=world_id, event_type="ELECTION",
                    actor_id=leader_id).order_by(WorldEvent.id).all():
                payload = json.loads(ev.payload) if ev.payload else {}
                if payload.get("organization_id") != o.id:
                    continue
                elections.append({
                    "day": ev.game_timestamp // 1440,
                    "winner": ev.actor_id,
                    "previous_leader": payload.get("previous_leader_id"),
                    "reason": payload.get("reason"),
                })
                leader_since_day = ev.game_timestamp // 1440

            org_entries.append({
                "name": o.name,
                "id": o.id,
                "leader_id": leader_id,
                "leader_since_day": leader_since_day,
                "member_count": member_count,
                "treasury_balance": treasury_balance,
                "active_law": law_row.law_key if law_row else None,
                "dues_collected_total": dues_collected_total,
                "feasts": feasts,
                "elections": elections,
            })
            sum_elections += len(elections)
            sum_violations += violations
            fines_total += org_fines_total
            fines_unpaid += org_fines_unpaid
            sum_feasts += feasts

        report["org"] = {
            "organizations": org_entries,
            "summary": {
                "elections": sum_elections,
                "violations": sum_violations,
                "fines_total": fines_total,
                "fines_unpaid": fines_unpaid,
                "feasts": sum_feasts,
                "reconciliations": sum_reconciliations,
            },
        }

    return report

