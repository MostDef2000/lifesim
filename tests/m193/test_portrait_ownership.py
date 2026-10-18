"""#193 — visual portrait ownership (R1) + single-canonical invariant (R2).

Contract under test (specs/006-flux R7/R8/R8.1, revised by #193):
- a canonical portrait linked to a PLAYER character (user_id not NULL) is
  readable ONLY by its owner; a foreign authenticated user gets the SAME
  404 as the canonical-miss path (no-disclosure, existence never
  confirmed) — and the same 404 on the asset-id bypass endpoints
  (GET /visual/assets/{id} and /file);
- a canonical portrait linked to an NPC (user_id NULL) stays any-auth —
  the #191 lazy-generation feature keeps working for everyone;
- POST /visual/assets/{id}/canonical (write primitive): a foreign user
  flipping a player-linked asset gets 403 "not your character" BEFORE any
  mutation (write-primitive convention, mirrors the generation gate);
- R2 single-canonical invariant, app-level (CharacterDesire pattern):
  promoting a portrait demotes every sibling canonical portrait of the
  same character in the same transaction (demote-before-promote), and the
  R10 integrity invariant flags any >1 canonical-portrait state.

History: reading another player's EXISTING canonical portrait was the
documented M6 behavior (R8.1 parenthetical); #193 closed it — the leak
exposed potentially nude player renders to every authenticated user
(prod lora pipeline, #205 wave), same disclosure family as #214/#215.
"""
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None

PW = "Correct-Horse-9!"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _make_settings(tmp_path):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w193.db")}
        ),
        "visual": visual,
    })


def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text


def _create_character(client, name):
    r = client.post("/characters", json={"name": name, "sex": "F", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestPortraitOwnership:
    def _build(self, tmp_path):
        from sqlalchemy.orm import sessionmaker

        from app.characters.generator import generate_population

        settings = _make_settings(tmp_path)
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            generate_population(
                session, settings, random.Random(42),
                settings.world.world_id, 20,
            )
            session.commit()
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        from app.api.app import create_app

        app = create_app(settings, factory)

        owner = TestClient(app)
        _register_and_login(owner, "w193owner")
        owner_cid = _create_character(owner, "Влада")

        other = TestClient(app)
        _register_and_login(other, "w193other")
        other_cid = _create_character(other, "Остап")

        return settings, factory, owner, other, owner_cid, other_cid

    @pytest.fixture()
    def env(self, tmp_path):
        return self._build(tmp_path)

    def _promote(self, client, asset_id):
        r = client.post(
            f"/visual/assets/{asset_id}/canonical", json={"canonical": True})
        assert r.status_code == 200, r.text
        # hardening (W193-RV N1): the promote must not silently no-op —
        # the response body pins the new canonical state
        assert r.json()["canonical"] is True, r.text
        return r

    def _make_canonical(self, client, cid):
        """Generate + promote one portrait for cid; returns the asset."""
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        asset = r.json()["asset"]
        self._promote(client, asset["id"])
        return asset

    def test_foreign_canonical_read_404(self, env):
        """R1 detector: another user's canonical portrait is 404
        (no-disclosure, same shape as the miss path)."""
        _, _, owner, other, owner_cid, _ = env
        self._make_canonical(owner, owner_cid)
        r = other.get(f"/visual/characters/{owner_cid}/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"

    def test_foreign_asset_id_bypass_404(self, env):
        """R1: the asset-id bypass (metadata + bytes) is closed with the
        SAME 404 the miss path uses."""
        _, _, owner, other, owner_cid, _ = env
        asset = self._make_canonical(owner, owner_cid)
        r_meta = other.get(f"/visual/assets/{asset['id']}")
        assert r_meta.status_code == 404, r_meta.text
        r_file = other.get(f"/visual/assets/{asset['id']}/file")
        assert r_file.status_code == 404, r_file.text

    def test_npc_canonical_still_public(self, env):
        """R1: NPC-linked canonical portraits stay any-auth (the #191
        lazy-generation contract is untouched)."""

        from app.db.models import Character

        settings, factory, owner, other, _, _ = env
        with factory() as session:
            npc = (
                session.query(Character)
                .filter_by(
                    world_id=settings.world.world_id,
                    user_id=None, alive=True)
                .order_by(Character.id)
                .first()
            )
            assert npc is not None
            npc_id = npc.id
        # any-auth GET triggers the lazy canonical generation (R8.1)
        r = other.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 200, r.text
        assert r.json()["canonical"] is True

    def test_owner_still_reads_own(self, env):
        """R1: the owner's own canonical read is unchanged."""
        _, _, owner, _, owner_cid, _ = env
        asset = self._make_canonical(owner, owner_cid)
        r = owner.get(f"/visual/characters/{owner_cid}/portrait")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == asset["id"]

    def test_set_canonical_foreign_403(self, env):
        """R1 write side: a foreign user cannot flip another player's
        asset canonical flag — 403 before any mutation."""
        _, _, owner, other, owner_cid, _ = env
        r = owner.post(f"/visual/portraits/{owner_cid}")
        assert r.status_code == 201, r.text
        asset_id = r.json()["asset"]["id"]
        before = other.get(f"/visual/assets/{asset_id}").status_code
        assert before == 404  # reads already gated; the write gate too:
        r = other.post(
            f"/visual/assets/{asset_id}/canonical",
            json={"canonical": True})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "not your character"

    def test_demote_before_promote(self, env):
        """R2: promoting the second portrait demotes the first in the same
        transaction — at most one canonical portrait per character."""

        from app.db.models import VisualAsset

        settings, factory, owner, _, owner_cid, _ = env
        r1 = owner.post(f"/visual/portraits/{owner_cid}")
        assert r1.status_code == 201, r1.text
        a1 = r1.json()["asset"]["id"]
        r2 = owner.post(f"/visual/portraits/{owner_cid}")
        assert r2.status_code == 201, r2.text
        a2 = r2.json()["asset"]["id"]
        assert a1 != a2
        self._promote(owner, a1)  # a1 becomes the canonical one
        self._promote(owner, a2)  # must demote a1 in the same transaction
        with factory() as session:
            flags = {
                row.id: row.canonical
                for row in session.query(VisualAsset)
                .filter(VisualAsset.id.in_([a1, a2]))
            }
        assert flags[a2] is True, flags
        assert flags[a1] is False, flags

    def test_invariant_flags_double_canonical(self, env):
        """R10: visual_asset_integrity flags >1 canonical portrait per
        character (DB-state check, endpoint-independent)."""

        from app.db.models import VisualAsset
        from app.simulation.invariants import run_invariant_checks

        settings, factory, owner, _, owner_cid, _ = env
        with factory() as session:
            session.add_all([
                VisualAsset(
                    world_id=settings.world.world_id,
                    asset_type="portrait", character_id=owner_cid,
                    scene_descriptor={"kind": "portrait"},
                    prompt="t1", storage_path="m193-a.png",
                    seed=1, model="stub", canonical=True,
                    created_at="0",
                ),
                VisualAsset(
                    world_id=settings.world.world_id,
                    asset_type="portrait", character_id=owner_cid,
                    scene_descriptor={"kind": "portrait"},
                    prompt="t2", storage_path="m193-b.png",
                    seed=2, model="stub", lora=None, canonical=True,
                    created_at="0",
                ),
            ])
            session.commit()
        with factory() as session:
            results = run_invariant_checks(
                session, settings.world.world_id, settings)
        visual = [r for r in results if r["name"] == "visual_asset_integrity"]
        assert len(visual) == 1
        assert visual[0]["ok"] is False
        assert "canonical portraits" in visual[0]["details"], visual[0]
