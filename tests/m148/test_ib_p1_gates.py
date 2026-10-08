"""#113 + #112 (IB P1): docs fail-closed + session-secret fail-closed gates.

One PR, two bounded security fixes, verified against base a584e7df (RED there):

- #113 — FastAPI served Swagger/redoc/openapi.json unconditionally. ApiConfig
  gains ``docs`` (fail-closed default False); default.yaml opts in for dev
  (m9 parity: /docs 200 under default.yaml), production.yaml pins
  ``docs: false``, and deploy/Caddyfile 404s the paths at the edge anyway
  (defense in depth — m15 pins those lines).
- #112 — get_secret failed open to the dev literal, and a bare secondary
  ``or os.getenv("VL1_SECRET")`` let the canonical var silently override a
  custom api.secret_env. Now: lookup uses ONLY the configured env name; with
  api.require_secret=true an unset var raises RuntimeError both at first use
  and at BOOT via the run._build guard (symmetric to the api.enabled guard;
  uvicorn's lifespan startup invokes the app → _build → refusal, non-zero).

Pin style follows m146/m147 (byte-pin lineage): previously green behavior
stays byte-pinned (m9 /docs parity, conftest dev fallback).
"""
import os
import re

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.app import get_secret
from app.config.config import ApiConfig, load_config
from app.db.models import create_engine_factory

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEFAULT_YAML = os.path.join(ROOT, "config", "default.yaml")
PRODUCTION_YAML = os.path.join(ROOT, "config", "production.yaml")

DEV_FALLBACK = "dev-insecure-secret-change-me"
SECRET_MSG = "not set; refusing to sign sessions with a dev secret (§security)"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _section(lines, header, nxt):
    """Slice lines between two top-level YAML headers (api: … visual:)."""
    return lines[lines.index(header):lines.index(nxt)]


# ---------- (a) config pins: ApiConfig + yaml keys ----------

def test_apicon_defaults_fail_closed():
    api = ApiConfig()
    assert api.docs is False  # #113: docs off unless a config opts in
    assert api.require_secret is False  # #112: dev fallback stays dev-only


def test_default_yaml_opts_into_dev_docs():
    """#113: default.yaml keeps Swagger for dev (m9 /docs 200 parity)."""
    lines = _read(DEFAULT_YAML).splitlines()
    assert "  docs: true" in _section(lines, "api:", "visual:")


def test_production_yaml_pins_docs_off_and_secret_required():
    lines = _read(PRODUCTION_YAML).splitlines()
    api = _section(lines, "api:", "visual:")
    assert "  docs: false" in api  # #113
    assert "  require_secret: true" in api  # #112


# ---------- (b) app factory: docs gate ----------

def _client_for(tmp_path, api_updates):
    """Build a TestClient from real config files (m9 fixture pattern)."""
    base = load_config(DEFAULT_YAML)
    settings = base.model_copy(update={
        "persistence": base.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "admin": base.admin.model_copy(update={"rate_limit_enabled": False}),
        "api": base.api.model_copy(update=api_updates),
    })
    engine = create_engine_factory(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    return TestClient(create_app(settings, factory))


class TestDocsGate:
    def test_production_style_docs_disabled_404(self, tmp_path):
        """#113: api.docs=false removes /docs, /redoc and /openapi.json."""
        client = _client_for(tmp_path, {"docs": False})
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        # the API itself still answers (gate is docs-only)
        assert client.get("/health").status_code == 200

    def test_default_yaml_docs_enabled_m9_parity(self, tmp_path):
        """m9 parity: default.yaml (docs: true) still serves /docs."""
        client = _client_for(tmp_path, {})
        assert client.get("/docs").status_code == 200


# ---------- (c) get_secret: fail-closed semantics ----------

class _ApiStub:
    def __init__(self, require_secret, secret_env="VL1_SECRET"):
        self.require_secret = require_secret
        self.secret_env = secret_env


class _StubSettings:
    def __init__(self, require_secret, secret_env="VL1_SECRET"):
        self.api = _ApiStub(require_secret, secret_env)


def test_get_secret_raises_when_required_and_unset(monkeypatch):
    monkeypatch.delenv("VL1_SECRET", raising=False)
    settings = _StubSettings(require_secret=True)
    # exact contract: named env + §security reason
    with pytest.raises(RuntimeError) as excinfo:
        get_secret(settings)
    assert str(excinfo.value) == (
        "api.secret_env 'VL1_SECRET' not set; refusing to sign sessions "
        "with a dev secret (§security)"
    )


def test_get_secret_returns_env_value_when_set(monkeypatch):
    monkeypatch.setenv("VL1_SECRET", "unit-test-not-a-real-secret")
    settings = _StubSettings(require_secret=True)
    assert get_secret(settings) == "unit-test-not-a-real-secret"


def test_get_secret_dev_fallback_when_not_required(monkeypatch):
    """conftest parity: require_secret=false keeps the dev fallback (no env)."""
    monkeypatch.delenv("VL1_SECRET", raising=False)
    assert get_secret(_StubSettings(require_secret=False)) == DEV_FALLBACK


def test_get_secret_custom_env_lookup_is_single_sourced(monkeypatch):
    """#112 secondary defect: VL1_SECRET must NOT override a custom env name."""
    monkeypatch.delenv("VL1_SECRET_ALT", raising=False)
    monkeypatch.setenv("VL1_SECRET", "canonical-lookalike")
    # not required → only the configured name is consulted, then dev fallback
    assert get_secret(
        _StubSettings(require_secret=False, secret_env="VL1_SECRET_ALT")
    ) == DEV_FALLBACK
    # required → unset custom name refuses even though VL1_SECRET is set
    with pytest.raises(RuntimeError, match="api.secret_env 'VL1_SECRET_ALT'"):
        get_secret(_StubSettings(require_secret=True, secret_env="VL1_SECRET_ALT"))


# ---------- (d) run.py boot guard (m125 lineage: refusal, never a hang) ----------

def _production_style_config(tmp_path):
    """default.yaml mutated to the production posture the guard must reject."""
    data = yaml.safe_load(_read(DEFAULT_YAML))
    data["api"]["enabled"] = True
    data["api"]["require_secret"] = True
    data["api"]["docs"] = False
    data["persistence"]["db_path"] = str(tmp_path / "w.db")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
    return cfg


def _boot_env(monkeypatch, tmp_path, cfg):
    import app.run as run_mod

    monkeypatch.setattr(run_mod, "_CONFIG", str(cfg))
    monkeypatch.setattr(run_mod, "_APP", None)
    monkeypatch.delenv("VL1_SECRET", raising=False)
    return run_mod


def test_run_build_refuses_missing_secret_at_boot(tmp_path, monkeypatch):
    """#112: prod refuses at BOOT (run._build), not at first request."""
    cfg = _production_style_config(tmp_path)
    run_mod = _boot_env(monkeypatch, tmp_path, cfg)
    with pytest.raises(RuntimeError, match=re.escape(SECRET_MSG)):
        run_mod._build()


def test_run_build_proceeds_when_secret_set(tmp_path, monkeypatch):
    """Positive control: same config + secret env → the app builds."""
    cfg = _production_style_config(tmp_path)
    run_mod = _boot_env(monkeypatch, tmp_path, cfg)
    monkeypatch.setenv("VL1_SECRET", "unit-test-not-a-real-secret")
    app = run_mod._build()
    assert app is not None
