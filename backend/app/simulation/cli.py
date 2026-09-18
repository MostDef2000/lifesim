import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.characters.generator import generate_population
from app.config.config import load_config
from app.db.models import bootstrap
from app.simulation.engine import Engine, TickScheduler, WorldClock
from app.simulation.report import build_report
from app.world.seed_world import seed_world


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lifesim Headless Simulation CLI")
    subparsers = parser.add_subparsers(dest="command")

    sim_parser = subparsers.add_parser("simulate")
    sim_parser.add_argument("--days", type=int, required=True)
    sim_parser.add_argument("--population", type=int, required=True)
    sim_parser.add_argument("--seed", type=int, required=True)
    sim_parser.add_argument("--config", type=str, default="config/default.yaml")
    sim_parser.add_argument("--out", type=str, default=None)
    sim_parser.add_argument("--social", action="store_true", default=False)
    sim_parser.add_argument("--org", action="store_true", default=False)
    sim_parser.add_argument("--llm", action="store_true", default=False)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config", type=str, default="config/default.yaml")
    serve_parser.add_argument(
        "--db", type=str, default=None,
        help="existing world DB path (overrides config)"
    )

    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--config", type=str, default="config/default.yaml")
    backup_parser.add_argument(
        "--db", type=str, default=None,
        help="existing world DB path (overrides config)"
    )
    backup_parser.add_argument(
        "--backup-dir", type=str, default=None,
        help="backup output directory (overrides config admin.backup_dir)"
    )
    backup_parser.add_argument(
        "--keep", type=int, default=None,
        help="retention count (overrides config admin.backup_keep)"
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _run_serve(args)

    if args.command == "backup":
        return _run_backup(args)

    if args.command != "simulate":
        parser.print_help()
        sys.exit(3)

    try:
        # 1. Load Config
        settings = load_config(args.config)

        # 2. Override population
        # settings is a Pydantic model, we use model_copy for a clean update
        settings = settings.model_copy(
            update={
                "world": settings.world.model_copy(update={"initial_population": args.population}),
                "social": settings.social.model_copy(update={"enabled": args.social}),
                "org": settings.org.model_copy(update={"enabled": args.org}),
                "llm": settings.llm.model_copy(update={"enabled": args.llm})
            }
        )

        # 3. Setup Database
        db_path = settings.persistence.db_path
        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Determinism: remove existing db for a fresh run
        if os.path.exists(db_path):
            os.remove(db_path)

        from app.db.models import create_engine_factory

        engine = create_engine_factory(settings)

        # 4. Bootstrap and Seed
        with Session(engine) as session:
            bootstrap(engine, settings, args.seed)
            world_id = settings.world.world_id
            seed_world(session, settings, world_id)

            # Seed population
            rng = random.Random(args.seed)
            generate_population(session, settings, rng, world_id, args.population)

            # M2: social seeding (org membership/leaders, seeded conflicts)
            from app.world.social_seed import seed_social
            seed_social(session, settings, world_id, rng)

            # M7: external Vladivostok (locations, services, contacts — no events)
            from app.world.seed_external import seed_external_world
            seed_external_world(session, settings, world_id, rng)

            # M3: initial laws enactment
            from app.policies.org import enact_initial_laws
            enact_initial_laws(session, world_id, settings, timestamp=0)

            # 5. Run Simulation
            clock = WorldClock()
            scheduler = TickScheduler()
            sim_engine = Engine(clock, scheduler)

            start_wall_time = time.time()

            # Step by day: 1440 minutes per day
            for _ in range(args.days):
                sim_engine.step(1440, session=session, world_id=world_id, settings=settings)

            session.commit()

            # 6. Report and Invariants
            wall_duration = time.time() - start_wall_time
            report = build_report(session, world_id, settings, wall_duration, args.seed)

            # Output report
            json_report = json.dumps(report, indent=2)
            if args.out:
                with open(args.out, "w") as f:
                    f.write(json_report)
            else:
                print(json_report)

            if report["invariants_ok"]:
                return 0
            else:
                return 2

    except Exception as e:
        print(f"Error during simulation: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 3


def _run_serve(args) -> int:
    """M5 (SPEC §104): start the web-api. Requires api.enabled=true (AE6''')."""
    try:
        settings = load_config(args.config)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 3

    if not settings.api.enabled:
        print(
            "Error: api.enabled=false in config; "
            "set api.enabled=true to run 'vl1 serve'.",
            file=sys.stderr,
        )
        return 2

    db_path = args.db or settings.persistence.db_path
    if not os.path.exists(db_path):
        print(
            f"Error: world DB not found at {db_path}; run 'vl1 simulate' first.",
            file=sys.stderr,
        )
        return 2

    settings = settings.model_copy(
        update={"persistence": settings.persistence.model_copy(update={"db_path": db_path})}
    )

    from app.api.app import create_app
    from app.db.models import create_engine_factory

    engine = create_engine_factory(settings)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, session_factory)

    import uvicorn

    uvicorn.run(app, host=settings.api.host, port=settings.api.port, log_level="info")
    return 0


def _run_backup(args) -> int:
    """M8 (§87): consistent snapshot of DB + visual assets, with retention.

    Uses sqlite3 Connection.backup (safe on a live WAL database — read-only
    on the source). Assets are copied if the directory exists. Keeps the
    newest `admin.backup_keep` DB backups; older files are removed.
    """
    import shutil
    import sqlite3
    from datetime import datetime as _dt
    from pathlib import Path

    try:
        settings = load_config(args.config)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 3

    db_path = args.db or settings.persistence.db_path
    if not os.path.exists(db_path):
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 2

    backup_dir = Path(args.backup_dir or settings.admin.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
    # collision-proof stamp (multiple backups within one second)
    suffix = 0
    while (backup_dir / f"world_{stamp}.db").exists():
        suffix += 1
        stamp = f"{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}_{suffix}"

    # 1. consistent DB snapshot
    db_backup = backup_dir / f"world_{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(str(db_backup))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    # 2. visual assets (§87: images may be backed up less often, but a full
    #    copy per run keeps the snapshot self-contained)
    assets_src = Path("data/visual_assets")
    if assets_src.exists():
        shutil.copytree(assets_src, backup_dir / f"assets_{stamp}")

    # 3. retention: keep newest backup_keep DB files
    backups = sorted(backup_dir.glob("world_*.db"))
    removed = 0
    keep = args.keep if args.keep is not None else settings.admin.backup_keep
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()
        removed += 1

    print(
        f"backup ok: {db_backup} "
        f"(assets: {'yes' if assets_src.exists() else 'none'}; "
        f"retention removed: {removed})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
