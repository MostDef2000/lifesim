"""M5 (#110): 422 validation errors must not echo sensitive request values.

Pins the RequestValidationError masking: token/secret-shaped inputs and raw
malformed-JSON bodies must not appear in the 422 response body, while
non-sensitive errors keep full diagnostic info (type/loc/msg/input).

Issue #309: the register/login endpoints are gone (404/405), so the E2E
422 probes run against POST /characters — its body param precedes
Depends(current_user), so the 422 fires pre-auth. The numeric-password echo
tests are deleted with the endpoints (no surviving sensitive field; the
masking itself is pinned by the unit test below).
"""

import json
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from tests.helpers import auth_client, make_user  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Bootstrapped world with seeded locations/jobs, no NPCs."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    # #309: /characters field-level 422s fire AFTER current_user (only
    # malformed-JSON 422s are pre-auth), so the probe client is authenticated.
    with factory() as session:
        user = make_user(session, "masker", "m@x.com")
    return settings, app, auth_client(app, settings, user)


# ---------- E2E: 422 masking (#110) ----------

def test_malformed_json_422_keeps_diagnostics(world):
    """Truncated JSON body: masking must not over-strip json_invalid diagnostics.

    Pins our handler on the json_invalid path: type/loc/msg (and ctx, when
    present) survive, and masking never re-introduces raw-body echo (#110).
    #309: retargeted to POST /characters (register/login gone).
    """
    _, _, client = world
    r = client.post(
        "/characters",
        content=b'{"name": "SEKRET_NAME_123"',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422, r.text
    first = r.json()["detail"][0]
    for key in ("type", "loc", "msg"):
        assert first.get(key), f"non-empty {key} expected, got: {first}"
    if "ctx" in first:
        assert first["ctx"], f"non-empty ctx expected when present, got: {first}"
    assert "SEKRET_NAME_123" not in r.text


def test_nonsensitive_422_keeps_diagnostic_info(world):
    """Non-sensitive validation errors keep type/loc/msg/input (debuggability).

    #309: retargeted to POST /characters (register/login gone).
    """
    _, _, client = world
    r = client.post("/characters", json={"name": 123, "sex": "M", "age": 30})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    first = detail[0]
    for key in ("type", "loc", "msg"):
        assert first.get(key), f"non-empty {key} expected, got: {first}"
    assert "input" in first, f"input expected to remain for non-sensitive fields: {first}"


def test_handler_masks_token_and_secret_named_fields():
    """Unit pin: token/secret-SHAPED loc names are masked (incl. compound names)."""
    from app.api.app import _masked_validation_handler

    class FakeExc:
        def errors(self):
            return [
                {"type": "string_type", "loc": ["body", "api_token"],
                 "msg": "Input should be a valid string", "input": "TOK_VALUE_1"},
                {"type": "string_type", "loc": ["body", "password_confirm"],
                 "msg": "Input should be a valid string", "input": "PW_VALUE_2"},
                {"type": "missing", "loc": ["body", "nickname"],
                 "msg": "Field required", "input": "NICK_VALUE_3"},
            ]

    resp = _masked_validation_handler(None, FakeExc())
    detail = json.loads(resp.body)["detail"]
    assert "input" not in detail[0] and detail[0]["loc"] == ["body", "api_token"]
    assert "input" not in detail[1] and detail[1]["loc"] == ["body", "password_confirm"]
    assert "input" in detail[2], "non-sensitive field must keep input"
