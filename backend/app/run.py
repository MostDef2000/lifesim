"""015: uvicorn entrypoint (deploy/lifesim.service ExecStart target).

Builds the FastAPI app from the config file named by LIFESIM_CONFIG
(default config/default.yaml). Secrets come from the environment
(VL1_SECRET, see .env.example / deploy/README.md).
"""
import os

from app.config.config import load_config

_CONFIG = os.getenv("LIFESIM_CONFIG", "config/default.yaml")

app = None


def _build():
    global app
    if app is None:
        settings = load_config(_CONFIG)
        from app.api.app import create_app

        app = create_app(settings)
    return app


# uvicorn app.run:app — module-level attribute access; defer build until
# first import completes (factory-style lazy init keeps import cheap).
class _AppProxy:  # pragma: no cover - deployment glue
    def __getattr__(self, name):
        return getattr(_build(), name)


app = _AppProxy()
