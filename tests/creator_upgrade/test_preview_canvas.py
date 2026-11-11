"""020 wave 4 (issue #316) — preview canvas stages (019 reopen).

Contract of record: specs/019-flux-baseline-quality/spec.md "REOPENED" +
specs/020-character-creator/spec.md (wave-4 canvas stages). The
`creator_vertical` canvas (512×768) is honored ONLY when the
SERVER-CONTROLLED capability flag (create_app kwarg → app.state) is on —
no user-supplied flag can enable it; the check fires BEFORE any transport
call; and it never combines with a reference image.

Covers (CaptureTransport pattern from m236 — every generate() call is
recorded with its size):
- canvas absent / "legacy" → the CONFIGURED image_size reaches the
  transport (byte-unchanged path).
- creator_vertical with capability OFF → 422 and the transport is NEVER
  called (pre-transport gate).
- unknown canvas value → 422, transport never called.
- isolated app with capability ON → "512x768" reaches the transport.
- ON + reference_id → 422 (transport never called).
- ON + legacy canvas → the configured size (the flag only unlocks the
  vertical value, it never changes the default).
- the capability default is OFF on a plainly-built app.
"""

from app.visual.transports import StubFluxTransport

BODY = {"sex": "F", "age": 27}


class CaptureTransport:
    """Records every generate() call (kwargs included), delegates to the
    stub so the response stays a real PNG."""

    def __init__(self):
        self.calls = []

    def generate(self, prompt, seed, size, negative=None, reference_b64=None):
        self.calls.append({
            "prompt": prompt, "seed": seed, "size": size,
            "negative": negative, "reference_b64": reference_b64,
        })
        return StubFluxTransport().generate(prompt, seed, size)


def _captured(app):
    capture = CaptureTransport()
    app.state.flux_transport_factory = lambda: capture
    return capture


class TestCanvasOff:
    def test_capability_default_is_off(self, make_env):
        settings, app, factory = make_env()
        assert app.state.creator_vertical_capability is False

    def test_canvas_absent_uses_configured_size(self, authed):
        settings, app, factory, client = authed("wcrcabsent")
        capture = _captured(app)
        r = client.post("/visual/preview", json=dict(BODY))
        assert r.status_code == 200, r.text
        assert [c["size"] for c in capture.calls] == [
            settings.visual.image_size]

    def test_canvas_legacy_uses_configured_size(self, authed):
        settings, app, factory, client = authed("wcrclegacy")
        capture = _captured(app)
        r = client.post("/visual/preview", json={**BODY, "canvas": "legacy"})
        assert r.status_code == 200, r.text
        assert [c["size"] for c in capture.calls] == [
            settings.visual.image_size]

    def test_creator_vertical_off_422_transport_never_called(self, authed):
        settings, app, factory, client = authed("wcrcroff")
        capture = _captured(app)
        r = client.post("/visual/preview",
                        json={**BODY, "canvas": "creator_vertical"})
        assert r.status_code == 422, r.text
        assert "not enabled" in r.json()["detail"]
        assert capture.calls == []  # the gate fires BEFORE any transport call

    def test_unknown_canvas_422_transport_never_called(self, authed):
        settings, app, factory, client = authed("wcrcunknown")
        capture = _captured(app)
        r = client.post("/visual/preview", json={**BODY, "canvas": "square"})
        assert r.status_code == 422, r.text
        assert capture.calls == []


class TestCanvasOn:
    def test_creator_vertical_reaches_transport(self, authed):
        # the ISOLATED test app enables the server-controlled capability
        settings, app, factory, client = authed("wcrcron", capability=True)
        capture = _captured(app)
        r = client.post("/visual/preview",
                        json={**BODY, "canvas": "creator_vertical"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data.keys()) == {"prompt", "seed", "image_base64"}
        assert [c["size"] for c in capture.calls] == ["512x768"]

    def test_on_with_reference_422_transport_never_called(self, authed):
        settings, app, factory, client = authed("wcrcrref", capability=True)
        capture = _captured(app)
        r = client.post("/visual/preview", json={
            **BODY, "canvas": "creator_vertical", "reference_id": 1})
        assert r.status_code == 422, r.text
        assert "reference" in r.json()["detail"]
        assert capture.calls == []

    def test_on_plus_legacy_uses_configured_size(self, authed):
        settings, app, factory, client = authed("wcrcrleg", capability=True)
        capture = _captured(app)
        r = client.post("/visual/preview", json={**BODY, "canvas": "legacy"})
        assert r.status_code == 200, r.text
        assert [c["size"] for c in capture.calls] == [
            settings.visual.image_size]
