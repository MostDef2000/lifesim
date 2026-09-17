"""M6 Chunk A: transports, descriptor, prompt, store — foundation unit tests."""
import os
import random
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import VisualConfig, load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.visual.store import AssetStore  # noqa: E402
from app.visual.transports import (  # noqa: E402
    HttpFluxTransport,
    StubFluxTransport,
    _render_placeholder_png,
    build_transport,
)

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world_two_characters(tmp_path):
    """Real world (M1 generator) — returns session + ids for descriptor tests."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    from app.db.models import Character, Location

    # Pick a location that actually hosts characters (root locations may be empty).
    location_ids = session.scalars(
        select(Location.id).order_by(Location.id)
    ).all()
    location_id = None
    for loc_id in location_ids:
        count = len(session.scalars(
            select(Character.id).where(Character.location_id == loc_id)
        ).all())
        if count >= 1:
            location_id = loc_id
            break
    assert location_id is not None, "no location with characters"
    char_ids = session.scalars(
        select(Character.id).where(Character.location_id == location_id).order_by(Character.id)
    ).all()
    return session, settings.world.world_id, char_ids, location_id


class TestStubTransport:
    def test_deterministic_bytes(self):
        stub = StubFluxTransport()
        a = stub.generate("portrait of A", 42, "512x512")
        b = stub.generate("portrait of A", 42, "512x512")
        assert a == b

    def test_different_seed_different_bytes(self):
        stub = StubFluxTransport()
        a = stub.generate("portrait of A", 42, "512x512")
        b = stub.generate("portrait of A", 43, "512x512")
        assert a != b

    def test_different_prompt_different_bytes(self):
        stub = StubFluxTransport()
        a = stub.generate("portrait of A", 42, "512x512")
        b = stub.generate("portrait of B", 42, "512x512")
        assert a != b

    def test_valid_png_magic(self):
        data = StubFluxTransport().generate("scene", 1, "64x64")
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert data.endswith(b"IEND\xaeB`\x82")

    def test_size_respected(self):
        small = _render_placeholder_png("s", 1, "8x8")
        big = _render_placeholder_png("s", 1, "16x16")
        assert len(big) > len(small)


class TestBuildTransport:
    def test_stub_selected(self):
        transport = build_transport(VisualConfig(transport="stub"))
        assert isinstance(transport, StubFluxTransport)

    def test_http_selected(self):
        transport = build_transport(VisualConfig(transport="http"))
        assert isinstance(transport, HttpFluxTransport)

    def test_unknown_rejected(self):
        with pytest.raises(ValueError, match="unknown visual transport"):
            build_transport(VisualConfig(transport="fax"))


class TestHttpTransport:
    def test_contract_post_and_bytes(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["data"] = request.data
            captured["timeout"] = timeout
            import io

            return io.BytesIO(b"\x89PNG fake-image-bytes")

        import urllib.request as urllib_request

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
        config = VisualConfig(
            transport="http", base_url="http://flux.internal:7860",
            model="flux.1-schnell", lora="vl1-style", timeout_sec=5.0,
        )
        data = HttpFluxTransport(config).generate("scene prompt", 7, "256x256")
        assert data == b"\x89PNG fake-image-bytes"
        assert captured["url"] == "http://flux.internal:7860/generate"
        assert b'"prompt": "scene prompt"' in captured["data"]
        assert b'"seed": 7' in captured["data"]
        assert b'"model": "flux.1-schnell"' in captured["data"]
        assert captured["timeout"] == 5.0


class TestDescriptor:
    def test_scene_descriptor_deterministic(self, world_two_characters):
        session, world_id, char_ids, location_id = world_two_characters
        from app.visual.descriptor import build_scene_descriptor

        d1 = build_scene_descriptor(session, world_id, location_id)
        d2 = build_scene_descriptor(session, world_id, location_id)
        assert d1 == d2
        assert d1["location"]["id"] == location_id
        assert len(d1["characters"]) >= 1
        assert [c["id"] for c in d1["characters"]] == sorted(c["id"] for c in d1["characters"])
        assert {c["id"] for c in d1["characters"]} <= set(char_ids)
        assert d1["camera"] == "wide"
        assert "time" in d1 and "day" in d1["time"]

    def test_unknown_location(self, world_two_characters):
        session, world_id, *_ = world_two_characters
        from app.visual.descriptor import build_scene_descriptor

        with pytest.raises(LookupError):
            build_scene_descriptor(session, world_id, "loc_missing")


class TestPromptBuilder:
    def test_scene_prompt_deterministic(self):
        from app.visual.descriptor import build_prompt

        descriptor = {
            "location": {"id": "loc_1", "name": "Harbor", "type": "port"},
            "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
            "objects": [{"id": "o1", "type": "crate"}],
            "weather": "clear",
            "time": {"day": 3, "hour": 9, "time_of_day": "morning"},
            "camera": "wide",
            "event": None,
            "references": ["visual/5.png"],
        }
        assert build_prompt(descriptor) == build_prompt(descriptor)
        assert "Harbor" in build_prompt(descriptor)
        assert "reference portraits: ['visual/5.png']" in build_prompt(descriptor)

    def test_portrait_prompt(self):
        from app.visual.descriptor import build_prompt

        descriptor = {
            "camera": "portrait",
            "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
        }
        prompt = build_prompt(descriptor)
        assert "adult fictional character" in prompt
        assert "Anna" in prompt


class TestAssetStore:
    def test_roundtrip(self, tmp_path):
        store = AssetStore(VisualConfig(storage_dir=str(tmp_path / "assets")))
        relative = store.save(5, b"png-bytes-5")
        assert relative == "5.png"
        assert store.read(relative) == b"png-bytes-5"
        assert store.exists(relative)

    def test_traversal_blocked(self, tmp_path):
        store = AssetStore(VisualConfig(storage_dir=str(tmp_path / "assets")))
        with pytest.raises(ValueError, match="unsafe storage path"):
            store.read("../secret.png")
        with pytest.raises(ValueError, match="unsafe storage path"):
            store.read("sub/dir/1.png")
