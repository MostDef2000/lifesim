"""#155 [observability] uvicorn entrypoints route app logs (vl1.* → journalctl).

`uvicorn app.run:app` and `vl1 serve` never configured application logging:
the root logger ran on lastResort (WARNING+), so INFO lines from vl1.ticker
("live tick driver started", lock-contention warning) never reached
stdout/stderr and therefore journalctl. Ticker itself always worked (flock +
moving clock prove it) — this is pure observability.

The fix lives in the ONE shared factory (app.api.app): _setup_app_logging()
gives the `vl1` logger a stderr StreamHandler marked with a private _vl1
attribute (idempotent across repeated create_app() calls — tests build many
apps), level INFO, propagate=False. uvicorn.* loggers stay untouched:
uvicorn configures its own access logs with propagate=False, and inflating
the journal is an explicit non-goal of #155.

Fixture pattern follows tests/m152 (real default.yaml + model_copy
overrides, tmp db) minus the live driver: these pins are about logging
configuration, so live_tick_enabled stays False for speed. cli.py and
run.py are READ ONLY here — the last test pins that both entrypoints build
through create_app() exactly once, inheriting this setup with zero changes
in those files.
"""

import ast
import io
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api import app as app_module  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import create_engine_factory  # noqa: E402

SETTINGS = None

# Source paths derived from the imported module — no cwd assumptions.
_APP_PY = Path(app_module.__file__).resolve()
_BACKEND_DIR = _APP_PY.parents[2]  # .../backend/app/api/app.py -> backend
_CLI_PY = _BACKEND_DIR / "app" / "simulation" / "cli.py"
_RUN_PY = _BACKEND_DIR / "app" / "run.py"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _build_app(tmp_path):
    """Minimal factory build (m152 _build_world pattern, driver disabled)."""
    settings = SETTINGS.model_copy(update={
        "world": SETTINGS.world.model_copy(update={"live_tick_enabled": False}),
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "obs.db")}
        ),
    })
    engine = create_engine_factory(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return create_app(settings, factory)


# ---------- 1) create_app configures the vl1 logger ----------

def test_create_app_configures_vl1_logger(tmp_path):
    _build_app(tmp_path)
    logger = logging.getLogger("vl1")
    assert logger.getEffectiveLevel() == logging.INFO
    # vl1.ticker inherits through the hierarchy (its own level stays NOTSET).
    assert logging.getLogger("vl1.ticker").getEffectiveLevel() == logging.INFO
    assert logger.handlers, "vl1 logger must carry at least one handler"
    assert all(getattr(h, "_vl1", False) for h in logger.handlers), (
        "every vl1 handler must carry the _vl1 idempotency marker"
    )
    assert logger.propagate is False


# ---------- 2) repeated create_app() never stacks duplicate handlers ----------

def test_repeated_create_app_does_not_stack_handlers(tmp_path):
    _build_app(tmp_path)
    logger = logging.getLogger("vl1")
    before = list(logger.handlers)
    assert before, "first build must have configured the vl1 logger"
    _build_app(tmp_path)  # tests build many apps on the same process
    assert list(logger.handlers) == before, (
        "repeated create_app() must not stack duplicate vl1 handlers"
    )


# ---------- 3) functional: vl1.ticker INFO reaches the handler ----------

def test_vl1_ticker_info_reaches_handler(tmp_path):
    _build_app(tmp_path)  # ensure the factory-configured logger exists
    logger = logging.getLogger("vl1")
    original = list(logger.handlers)
    assert original, "factory must have configured the vl1 logger"
    buffer = io.StringIO()
    probe = logging.StreamHandler(buffer)
    probe._vl1 = True
    probe.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    try:
        logger.handlers = [probe]
        logging.getLogger("vl1.ticker").info("live tick driver started: test")
    finally:
        logger.handlers = original
    captured = buffer.getvalue()
    assert "live tick driver started: test" in captured
    assert "vl1.ticker" in captured
    assert "INFO" in captured


# ---------- 4) uvicorn loggers are NOT touched; call-site pins ----------

def test_uvicorn_loggers_untouched():
    src = _APP_PY.read_text(encoding="utf-8")
    # uvicorn configures its own access logs with propagate=False — the app
    # must not add handlers/levels to uvicorn.* loggers (#155 non-goal).
    assert "uvicorn.access" not in src
    assert src.count('getLogger("uvicorn') == 0
    # The helper is defined once and called once from create_app.
    assert src.count("_setup_app_logging()") == 2, (
        "expected exactly the def line and the create_app call site"
    )
    assert src.count('logging.getLogger("vl1")') == 1


# ---------- 5) both entrypoints route through create_app (READ ONLY) -----

def test_cli_and_run_entrypoints_route_through_create_app():
    """Pins cli.py and run.py WITHOUT editing them: each uvicorn entrypoint
    must call create_app exactly once, so both inherit the #155 logging
    setup with zero changes there. Counted as real call sites via ast —
    run.py's module docstring also mentions create_app( in prose, so a raw
    text count there is 2, not 1."""
    for path in (_CLI_PY, _RUN_PY):
        src = path.read_text(encoding="utf-8")
        assert "create_app(" in src, f"{path.name} never references create_app"
        tree = ast.parse(src)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
            and (getattr(node.func, "id", None) == "create_app"
                 or getattr(node.func, "attr", None) == "create_app")
        ]
        assert len(calls) == 1, (
            f"{path.name} must call create_app exactly once, found {len(calls)}"
        )
