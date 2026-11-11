"""Shared env for the 020 wave-4 creator-upgrade suites (issue #316).

Mirrors the m216 `_env` pattern: seeded world + a FRESH app per test (the
draft rate-limiter/buckets and busy flags live in the register_creator_routes
closure; the preview limiter in register_visual_routes — a fresh app per test
is the isolation mechanism). No __init__.py (m236 precedent); conftest.py is
the pytest-native way to share the builder across the four suites.
"""
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tests.helpers import auth_client, make_user  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.characters.generator import generate_population  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.visual.transports import StubFluxTransport  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

_BASE_SETTINGS = None


def _base_settings():
    global _BASE_SETTINGS
    if _BASE_SETTINGS is None:
        _BASE_SETTINGS = load_config("config/default.yaml")
    return _BASE_SETTINGS


@pytest.fixture
def make_env(tmp_path):
    """Build (settings, app, factory) on a fresh seeded world.

    - llm_enabled: the draft endpoint's 503 gate reads settings.llm.enabled.
    - capability: the SERVER-CONTROLLED creator_vertical canvas flag
      (create_app kwarg → app.state) — production never passes it.
    - visual_enabled: the preview gate.
    A fresh app per test == fresh limiter buckets / busy flags.
    """

    def _make(*, llm_enabled=True, capability=False, visual_enabled=True):
        base = _base_settings()
        settings = base.model_copy(update={
            "persistence": base.persistence.model_copy(
                update={"db_path": str(tmp_path / "w_creator.db")}),
            "visual": base.visual.model_copy(update={
                "enabled": visual_enabled,
                "storage_dir": str(tmp_path / "assets"),
                "image_size": "8x8",
            }),
            "llm": base.llm.model_copy(update={"enabled": llm_enabled}),
        })
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            generate_population(session, settings, random.Random(42),
                                settings.world.world_id, 20)
            session.commit()
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        app = create_app(settings, factory,
                         creator_vertical_capability=capability)
        app.state.flux_transport_factory = lambda: StubFluxTransport()
        return settings, app, factory

    return _make


@pytest.fixture
def authed(make_env):
    """(settings, app, factory, client) with a fresh authed user — the m216
    `_authed` pattern (user row created directly, app's own signed cookie)."""

    def _make(username, **kwargs):
        settings, app, factory = make_env(**kwargs)
        with factory() as session:
            user = make_user(session, username, f"{username}@x.com")
        return settings, app, factory, auth_client(app, settings, user)

    return _make
