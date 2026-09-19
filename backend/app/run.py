"""015: uvicorn entrypoint (deploy/lifesim.service ExecStart target).

Mirrors `vl1 serve` (app.simulation.cli:_run_serve, SPEC §104): load config
from LIFESIM_CONFIG (default config/default.yaml), build the engine +
sessionmaker from settings.persistence, then create_app(settings,
session_factory). Secrets come from the environment (VL1_SECRET, see
.env.example / deploy/README.md).
"""
import os

from sqlalchemy.orm import sessionmaker

from app.config.config import load_config

_CONFIG = os.getenv("LIFESIM_CONFIG", "config/default.yaml")

_APP = None


def _build():
    global _APP
    if _APP is None:
        settings = load_config(_CONFIG)
        if not settings.api.enabled:
            raise RuntimeError(
                "api.enabled=false in config; "
                "deploy config (e.g. config/production.yaml) must set api.enabled=true"
            )
        from app.db.models import create_engine_factory

        engine = create_engine_factory(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        from app.api.app import create_app

        _APP = create_app(settings, factory)
    return _APP


# uvicorn app.run:app — module-level attribute access; defer build until
# first import completes (factory-style lazy init keeps import cheap).
# __call__ must be a real method: ASGI invokes app(scope, receive, send) and
# special methods bypass instance __getattr__.
class _AppProxy:  # pragma: no cover - deployment glue
    def __getattr__(self, name):
        return getattr(_build(), name)

    def __call__(self, scope, receive, send):
        return _build()(scope, receive, send)


app = _AppProxy()
