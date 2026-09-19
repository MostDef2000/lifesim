from collections import deque
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.models import Job, Location, LocationLink, Organization
from app.economy import mint, open_account
from app.inventory import adjust_resource


def seed_world(session: Session, settings, world_id: str) -> None:
    """
    Seeds the world with locations, links, organizations, accounts, and jobs.
    Deterministic: inserts in config order.
    """
    # 1. Locations
    # We use a temporary map to resolve config keys to database IDs.
    key_to_id = {}

    # Create locations in the order they appear in the config.
    for loc_key, params in settings.locations.locations.items():
        parent_id = key_to_id.get(params.parent)

        loc = Location(
            world_id=world_id,
            type=params.type,
            name=params.name,
            parent_id=parent_id,
            capacity=params.capacity
        )
        session.add(loc)
        session.flush()
        key_to_id[loc_key] = loc.id

    # Now create the N houses
    for i in range(settings.locations.houses_count):
        parent_id = key_to_id.get("settlement")
        house = Location(
            world_id=world_id,
            type="house",
            name=f"House {i+1}",
            parent_id=parent_id,
            capacity=1
        )
        session.add(house)
        session.flush()

    # Houses are reachable: every house-type location (config-defined "home"
    # and generated houses) gets a bidirectional link to its parent with the
    # config default travel time (houses are not in movement.edges).
    house_min = settings.movement.default_travel_minutes
    for house_loc in (
        session.query(Location)
        .filter_by(world_id=world_id, type="house")
        .order_by(Location.id)
        .all()
    ):
        if house_loc.parent_id is None:
            continue
        session.add(LocationLink(
            world_id=world_id,
            from_location_id=house_loc.parent_id,
            to_location_id=house_loc.id,
            travel_minutes=house_min
        ))
        session.add(LocationLink(
            world_id=world_id,
            from_location_id=house_loc.id,
            to_location_id=house_loc.parent_id,
            travel_minutes=house_min
        ))

    session.flush()

    # 2. Location Links
    for edge in settings.movement.edges:
        from_id = key_to_id.get(edge["from"])
        to_id = key_to_id.get(edge["to"])
        travel_min = edge["travel_minutes"]

        if from_id is not None and to_id is not None:
            # Create bidirectional links
            link1 = LocationLink(
                world_id=world_id,
                from_location_id=from_id,
                to_location_id=to_id,
                travel_minutes=travel_min
            )
            session.add(link1)

            link2 = LocationLink(
                world_id=world_id,
                from_location_id=to_id,
                to_location_id=from_id,
                travel_minutes=travel_min
            )
            session.add(link2)

    session.flush()

    # 3. Organizations
    org_map = {} # {config_key: org_id}
    # 016 (§35): police exists only when the crime system is enabled (П2)
    if getattr(getattr(settings, "crime", None), "enabled", False):
        police_loc = (
            session.query(Location)
            .filter_by(world_id=world_id, type="settlement")
            .first()
        )
        police = Organization(
            world_id=world_id, name="Полиция Рейнеке",
            type="security", location_id=police_loc.id
            if police_loc is not None else None,
        )
        session.add(police)
        session.flush()
        org_map["police"] = police.id
        police_account = open_account(session, world_id, "organization", str(police.id))
        mint(session, world_id, 0, police_account.id,
             settings.crime.police_starting_balance, "Police funds")
    for org_key, params in settings.economy.organizations.items():
        loc_id = key_to_id.get(params["location"])

        org = Organization(
            world_id=world_id,
            name=params["name"],
            type=params["type"],
            location_id=loc_id
        )
        session.add(org)
        session.flush()
        org_map[org_key] = org.id

        # Open account for organization
        account = open_account(session, world_id, "organization", str(org.id))

        # Initial organization funds (mint from NULL = ledger creation).
        # Employers need working capital to pay daily salaries.
        starting_balance = int(params.get("starting_balance", 0))
        if starting_balance > 0:
            mint(
                session, world_id, 0, account.id,
                starting_balance, "Initial organization funds"
            )

    # World account
    open_account(session, world_id, "world", "world_main")

    # 4. Water Resource Balance for community org
    community_org_id = org_map.get("community")
    if community_org_id:
        adjust_resource(
            session, world_id, "water", "organization", str(community_org_id),
            settings.economy.water_initial_quantity, 0
        )

    # 4.1 Seed WorldObject stock (Kitchen and Shop)
    # Deterministic: process in config dict order.
    # Kitchen stock
    # Kitchen stock is stored at the kitchen location (per plan the kitchen
    # is its own location; fall back to settlement if absent).
    kitchen_loc_id = key_to_id.get("kitchen", key_to_id.get("settlement"))
    # Note: If 'kitchen' is a specific location in config, use that.
    # In default_settings, 'settlement' is the common area.

    # To be precise, if there's a "kitchen" location, use it. Otherwise settlement.
    kitchen_loc_id = key_to_id.get("kitchen") or key_to_id.get("settlement")

    for obj_type, qty in settings.economy.kitchen_stock.items():
        from app.inventory import create_object
        create_object(
            session, world_id, object_type=obj_type, quantity=qty,
            owner_organization_id=community_org_id, location_id=kitchen_loc_id,
            metadata={}
        )

    # Shop stock
    shop_loc_id = key_to_id.get("shop")
    shop_org_id = org_map.get("shop")
    for obj_type, qty in settings.economy.shop_stock.items():
        from app.inventory import create_object
        create_object(
            session, world_id, object_type=obj_type, quantity=qty,
            owner_organization_id=shop_org_id, location_id=shop_loc_id,
            metadata={}
        )

    # 5. Jobs
    title_to_org = {
        "Farmer": "community",
        "Fisher": "community",
        "Crafter": "shop",
        "Storekeeper": "shop"
    }

    for job_data in settings.population.jobs:
        title = job_data["title"]
        org_key = title_to_org.get(title, "community")
        org_id = org_map.get(org_key)

        loc_key = "settlement" if org_key == "community" else "shop"
        loc_id = key_to_id.get(loc_key)

        job = Job(
            organization_id=org_id,
            title=title,
            salary=job_data["salary"],
            schedule=str(job_data["schedule"]),
            location_id=loc_id
        )
        session.add(job)

    session.flush()

def find_path(
    session: Session, world_id: str, from_location_id: int, to_location_id: int
) -> Tuple[Optional[List[int]], Optional[int]]:
    """
    BFS over location_links to find the shortest path.
    Neighbors are iterated in ascending id for determinism.
    Returns (path_ids, total_minutes) or (None, None).
    """
    if from_location_id == to_location_id:
        return ([from_location_id], 0)

    queue = deque([(from_location_id, [from_location_id], 0)])
    visited = {from_location_id}

    while queue:
        curr_id, path, total_time = queue.popleft()

        # Get neighbors, sorted by id for determinism
        links = (
            session.query(LocationLink)
            .filter_by(world_id=world_id, from_location_id=curr_id)
            .order_by(LocationLink.to_location_id)
            .all()
        )

        for link in links:
            neighbor = link.to_location_id
            if neighbor not in visited:
                visited.add(neighbor)
                new_path = path + [neighbor]
                new_time = total_time + link.travel_minutes

                if neighbor == to_location_id:
                    return (new_path, new_time)

                queue.append((neighbor, new_path, new_time))

    return (None, None)
