import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session

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

    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
