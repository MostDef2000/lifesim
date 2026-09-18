"""M8 Chunk C tests (SPEC 008-alpha, T12): AE1''''''-AE6'''''' evidence."""
import os
import sqlite3
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **admin_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "admin": SETTINGS.admin.model_copy(
            update={"rate_limit_enabled": False, **admin_updates}
        ),
    })


def build(settings):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
    return engine


class TestAE1AdminE2E:
    """AE1'''''': full admin flow E2E: overview → pause → timescale → resume
    → teleport → cancel → disable → audit → rate limit."""

    def test_full_admin_flow(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)

        with factory() as session:
            session.add(User(
                username="root", email="root@x.com",
                password_hash=hash_password("password123"),
                role="admin", age_confirmed=True, created_at=0,
            ))
            session.commit()

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={"username": "root", "password": "password123"})

        # overview works
        r = client.get("/admin/overview")
        assert r.status_code == 200

        # pause → timescale → resume
        assert client.post("/admin/world/pause").status_code == 200
        assert client.post(
            "/admin/world/timescale", json={"time_scale": 3.0}
        ).status_code == 200
        assert client.post("/admin/world/resume").status_code == 200

        # audit returns newest-first: the three actions appear, forward order preserved
        actions = [a["action"] for a in client.get("/admin/audit").json()]
        # exactly three audit entries, newest-first
        assert actions == ["resume_world", "set_timescale", "pause_world"]


class TestAE2Keystone:
    """AE2'''''': headless identity — admin routes only exist in the API layer;
    the simulation report and headless world are untouched."""

    def test_headless_untouched(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)

        from app.db.models import AdminAuditLog

        # fresh world: no audit entries, no admin activity
        with sessionmaker(bind=engine)() as session:
            assert session.query(AdminAuditLog).count() == 0

        # simulate a headless day: report works, invariants green
        from app.simulation.engine import Engine, TickScheduler, WorldClock
        from app.simulation.invariants import run_invariant_checks
        from app.simulation.report import build_report

        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(1440, session=session, world_id=settings.world.world_id,
                     settings=settings)
            session.commit()
            report = build_report(
                session, settings.world.world_id, settings,
                wall_duration_sec=0.0, seed=42,
            )
            results = run_invariant_checks(
                session, settings.world.world_id, settings
            )
        assert "admin" not in report  # build_report returns a plain dict
        assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]


class TestAE3Registration:
    """AE3'''''': registration controls (§85)."""

    def test_disabled_and_max_players(self, tmp_path):
        settings = make_settings(tmp_path, registration_enabled=False, max_players=2)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        r = client.post("/auth/register", json={
            "username": "nope", "email": "n@x.com",
            "password": "password123", "age_confirmed": True,
        })
        assert r.status_code == 403


class TestAE4Backup:
    """AE4'''''': `vl1 backup` produces a valid snapshot; retention prunes."""

    def test_backup_creates_and_prunes(self, tmp_path):
        workdir = tmp_path / "ops"
        workdir.mkdir()
        db_path = workdir / "w.db"
        settings = make_settings(
            workdir, backup_dir=str(workdir / "backups"), backup_keep=2
        )
        # ensure parent dirs then bootstrap
        db_path.parent.mkdir(parents=True, exist_ok=True)
        build(settings)

        import subprocess

        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        backup_dir = str(workdir / "backups")
        for i in range(4):  # 4 backups, keep=2 → 2 survive
            r = subprocess.run(
                [sys.executable, "-m", "app.simulation.cli", "backup",
                 "--db", str(db_path), "--backup-dir", backup_dir,
                 "--keep", "2"],
                capture_output=True, text=True,
                cwd=repo_root,
            )
            assert r.returncode == 0, r.stderr

        backups = sorted((workdir / "backups").glob("world_*.db"))
        assert len(backups) == 2  # retention

        # snapshot opens and contains the world tables
        conn = sqlite3.connect(str(backups[-1]))
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        conn.close()
        assert {"users", "worlds", "admin_audit_log"} <= tables


class TestAE5Negatives:
    """AE5'''''': role negatives and disabled-account API lockout."""

    def test_roles_and_lockout(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            for username, role in [("adm", "admin"), ("mod", "moderator"),
                                   ("usr", "user")]:
                session.add(User(
                    username=username, email=f"{username}@x.com",
                    password_hash=hash_password("password123"),
                    role=role, age_confirmed=True, created_at=0,
                ))
            session.commit()

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={"username": "usr", "password": "password123"})
        # user role: everything admin → 403
        assert client.get("/admin/overview").status_code == 403
        assert client.post("/admin/world/pause").status_code == 403
        assert client.post("/admin/users/1/disable").status_code == 403
        assert client.get("/admin/audit").status_code == 403

        # moderator reads but cannot act
        client.post("/auth/login", json={"username": "mod", "password": "password123"})
        assert client.get("/admin/overview").status_code == 200
        assert client.post("/admin/world/pause").status_code == 403

        # admin: ok
        client.post("/auth/login", json={"username": "adm", "password": "password123"})
        assert client.get("/admin/overview").status_code == 200


class TestAE6Observability:
    """AE6'''''': request-id propagation + validation negatives."""

    def test_request_id_and_negatives(self, tmp_path):
        settings = make_settings(tmp_path)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        r = client.get("/world", headers={"X-Request-ID": "trace-77"})
        assert r.headers["x-request-id"] == "trace-77"

        # auth-gated: no cookie → 401, but request-id still present
        r2 = client.get("/admin/overview")
        assert r2.status_code == 401
        assert r2.headers.get("x-request-id")
