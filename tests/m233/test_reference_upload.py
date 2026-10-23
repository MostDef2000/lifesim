"""020 P3 — visual reference upload (stage 1: storage + CRUD API).

Contract under test (specs/020-character-creator/p3-reference-upload.mini-spec.md):
- POST /visual/references: multipart field `file`; validation order
  auth → size cap (reference_max_bytes, chunked read) →
  magic bytes (PNG/JPEG) → Pillow full decode (verify + reopen, canvas
  ≤ 24 MP) → per-user quota (max_references_per_user).
  201 {id, mime, size_bytes, created_at}.
- GET /visual/references: own list, newest first.
- GET /visual/references/{id}/file: owner only (foreign/missing → 404,
  no existence leak), served with the stored mime.
- DELETE /visual/references/{id}: owner only, removes file + row, 204.
- Storage: data/visual_references via VisualReferenceStore, original bytes
  never re-encoded; path always server-generated ({ref_id}.{png|jpg}) —
  never taken from the client filename.
- References are guidance only: never simulation facts, never fed to the LLM
  (consumption/generation is a separate, deferred stage).

Fixture notes: images are built AT RUNTIME with Pillow (8x8) — no binary
fixtures in the repo. RED discipline: on base 9cb8844 the routes do not
exist, so every case fails on 404 (route missing), not on assertions.
"""
import os
import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _make_settings(tmp_path, visual_extra=None):
    visual_updates = {
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "references_dir": str(tmp_path / "references"),
    }
    if visual_extra:
        visual_updates.update(visual_extra)
    visual = SETTINGS.visual.model_copy(update=visual_updates)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w233.db")}
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


def _build(tmp_path, visual_extra=None):
    from sqlalchemy.orm import sessionmaker

    from app.api.app import create_app
    from app.db.models import bootstrap, create_engine_factory
    from app.world.seed_world import seed_world

    settings = _make_settings(tmp_path, visual_extra=visual_extra)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    return settings, engine, factory, app, client


def _png_bytes():
    """Real 8x8 PNG, built at runtime — never a committed binary fixture."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes():
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), (30, 200, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _corrupt_png_body():
    """Valid 8-byte PNG magic followed by garbage — Pillow must reject."""
    return PNG_MAGIC + b"this is not really a png body at all"


def _upload(client, data, filename="ref.png", mime="image/png"):
    return client.post("/visual/references", files={"file": (filename, data, mime)})


class TestReferenceUpload:
    def test_a_happy_path_roundtrip(self, tmp_path):
        """(A) upload PNG → 201 + id; list shows 1; file served image/png;
        DELETE → 204; file after delete → 404."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233a")
        r = _upload(client, _png_bytes())
        assert r.status_code == 201, r.text
        body = r.json()
        ref_id = body["id"]
        assert body["mime"] == "image/png"
        assert body["size_bytes"] == len(_png_bytes())
        assert body["created_at"]

        r = client.get("/visual/references")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["id"] == ref_id

        r = client.get(f"/visual/references/{ref_id}/file")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert r.content == _png_bytes()

        r = client.delete(f"/visual/references/{ref_id}")
        assert r.status_code == 204, r.text
        r = client.get(f"/visual/references/{ref_id}/file")
        assert r.status_code == 404, r.text

    def test_b_wrong_magic_rejected(self, tmp_path):
        """(B) text bytes → 422 unsupported reference format."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233b")
        r = _upload(client, b"just some text, definitely not an image")
        assert r.status_code == 422, r.text
        assert "unsupported reference format" in r.text

    def test_c_png_magic_corrupt_body_rejected(self, tmp_path):
        """(C) valid magic + garbage → 422 (full decode check rejects)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233c")
        r = _upload(client, _corrupt_png_body())
        assert r.status_code == 422, r.text
        assert "unsupported reference format" in r.text

    def test_d_oversized_rejected_nothing_stored(self, tmp_path):
        """(D) >5 MiB body → 422 reference too large; nothing stored."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233d")
        body = PNG_MAGIC + b"\x00" * (settings.visual.reference_max_bytes + 1)
        r = _upload(client, body)
        assert r.status_code == 422, r.text
        assert "reference too large" in r.text
        r = client.get("/visual/references")
        assert r.json()["items"] == []

    def test_e_quota(self, tmp_path):
        """(E) 5 uploads ok, 6th → 422 quota; after one delete 6th → 201."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233e")
        ids = []
        for i in range(5):
            r = _upload(client, _png_bytes(), filename=f"q{i}.png")
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        r = _upload(client, _png_bytes(), filename="q6.png")
        assert r.status_code == 422, r.text
        assert "reference quota exceeded" in r.text

        r = client.delete(f"/visual/references/{ids[0]}")
        assert r.status_code == 204, r.text
        r = _upload(client, _png_bytes(), filename="q6.png")
        assert r.status_code == 201, r.text

    def test_f_foreign_access_is_404(self, tmp_path):
        """(F) user B GETs/DELETEs user A's reference → 404 (no leak)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233fa")
        r = _upload(client, _png_bytes())
        assert r.status_code == 201, r.text
        ref_id = r.json()["id"]

        other = TestClient(app)
        _register_and_login(other, "m233fb")
        r = other.get(f"/visual/references/{ref_id}/file")
        assert r.status_code == 404, r.text
        r = other.delete(f"/visual/references/{ref_id}")
        assert r.status_code == 404, r.text
        # the row and file survive the foreign attempt
        r = client.get(f"/visual/references/{ref_id}/file")
        assert r.status_code == 200, r.text

    def test_g_unauthenticated_is_401(self, tmp_path):
        """(G) no token → 401 on POST/GET/DELETE (project auth convention)."""
        settings, engine, factory, app, client = _build(tmp_path)
        anon = TestClient(app)
        r = anon.post("/visual/references", files={
            "file": ("x.png", _png_bytes(), "image/png")})
        assert r.status_code == 401, r.text
        r = anon.get("/visual/references")
        assert r.status_code == 401, r.text
        r = anon.get("/visual/references/1/file")
        assert r.status_code == 401, r.text
        r = anon.delete("/visual/references/1")
        assert r.status_code == 401, r.text

    def test_h_jpeg_happy_path(self, tmp_path):
        """(H) JPEG upload served as image/jpeg."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233h")
        r = _upload(client, _jpeg_bytes(), filename="p.jpg", mime="image/jpeg")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["mime"] == "image/jpeg"
        r = client.get(f"/visual/references/{body['id']}/file")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == _jpeg_bytes()

    def test_i_lists_are_user_scoped(self, tmp_path):
        """(I) two users: each list shows only own references."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233ia")
        r = _upload(client, _png_bytes())
        assert r.status_code == 201, r.text
        a_id = r.json()["id"]

        other = TestClient(app)
        _register_and_login(other, "m233ib")
        r = other.post("/visual/references", files={
            "file": ("b.jpg", _jpeg_bytes(), "image/jpeg")})
        assert r.status_code == 201, r.text
        b_id = r.json()["id"]

        a_items = client.get("/visual/references").json()["items"]
        b_items = other.get("/visual/references").json()["items"]
        assert [i["id"] for i in a_items] == [a_id]
        assert [i["id"] for i in b_items] == [b_id]

    def test_j_client_filename_never_used_for_storage(self, tmp_path):
        """(J) same original filename twice → both stored, distinct ids,
        no collision: storage path is always {ref_id}.{ext}."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233j")
        r1 = _upload(client, _png_bytes(), filename="same-name.png")
        r2 = _upload(client, _jpeg_bytes(), filename="same-name.png")
        assert r1.status_code == 201, r1.text
        assert r2.status_code == 201, r2.text
        assert r1.json()["id"] != r2.json()["id"]
        r = client.get(
            f"/visual/references/{r2.json()['id']}/file")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"

    def test_k_gate_off_is_503(self, tmp_path):
        """(K) visual.enabled=false → reference routes behind the same 503
        gate as the rest of /visual."""
        settings, engine, factory, app, client = _build(
            tmp_path, visual_extra={"enabled": False})
        _register_and_login(client, "m233k")
        r = _upload(client, _png_bytes())
        assert r.status_code == 503, r.text
        assert "visual generation disabled" in r.text

    def test_l_mime_is_server_derived(self, tmp_path):
        """(L) client part content-type is never trusted: PNG bytes with a
        lying image/jpeg part → stored/served as image/png."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233l")
        r = _upload(client, _png_bytes(), filename="lie.png",
                    mime="image/jpeg")
        assert r.status_code == 201, r.text
        ref_id = r.json()["id"]
        assert r.json()["mime"] == "image/png"
        r = client.get("/visual/references")
        assert r.json()["items"][0]["mime"] == "image/png"
        r = client.get(f"/visual/references/{ref_id}/file")
        assert r.headers["content-type"] == "image/png"

    def test_m_orphan_file_recovery(self, tmp_path):
        """(M) file missing on disk: GET file → 404 'reference file
        missing'; DELETE still 204 and removes the row."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233m")
        r = _upload(client, _png_bytes())
        assert r.status_code == 201, r.text
        ref_id = r.json()["id"]
        orphan = Path(settings.visual.references_dir) / f"{ref_id}.png"
        assert orphan.exists()
        orphan.unlink()
        r = client.get(f"/visual/references/{ref_id}/file")
        assert r.status_code == 404, r.text
        assert "reference file missing" in r.text
        r = client.delete(f"/visual/references/{ref_id}")
        assert r.status_code == 204, r.text
        r = client.get("/visual/references")
        assert r.json()["items"] == []

    def test_n_oversized_canvas_rejected(self, tmp_path):
        """(N) decompression-bomb guard: 36 MP canvas (6000x6000) exceeds
        the 24 MP dims cap → 422 before any decode."""
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (6000, 6000), (10, 10, 10)).save(buf, format="PNG")
        body = buf.getvalue()
        assert len(body) < 5 * 1024 * 1024  # size cap is not what fires

        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m233n")
        r = _upload(client, body)
        assert r.status_code == 422, r.text
        assert "unsupported reference format" in r.text
        r = client.get("/visual/references")
        assert r.json()["items"] == []
