"""020 P3 stage 2 — reference_b64 rides the transport contract.

Contract under test (backend/app/visual/transports.py, established
optional-field pattern from tests/m227/test_negative_transport.py):
- FluxTransport.generate (the Protocol) and HttpFluxTransport.generate gain
  a trailing keyword param ``reference_b64=None``.
- reference_b64 is None (the default) -> the JSON payload is BYTE-IDENTICAL
  to the current one: exactly the 6 keys
  {prompt, seed, size, model, lora, negative}, no reference_b64 key at all.
- reference_b64 set -> the payload carries "reference_b64" with that exact
  value (the GPU adapter reads it from the same /generate JSON).

FakeTransport (the API-test seam) captures the field in
tests/m234/test_reference_consume_api.py — this file pins the real HTTP
transport wire format.

RED discipline: on base 0a2a25e the keyword does not exist (TypeError on
the call, missing parameter in the signature) while the no-reference
payload pin passes by design (regression guard).
"""
import base64
import inspect
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import VisualConfig  # noqa: E402
from app.visual.transports import FluxTransport, HttpFluxTransport  # noqa: E402

REF_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nreference").decode()
LEGACY_KEYS = ["prompt", "seed", "size", "model", "lora", "negative"]


def _config():
    return VisualConfig(
        transport="http", base_url="http://flux.internal:7860",
        model="flux.1-schnell", lora="vl1-style", timeout_sec=5.0,
    )


def _capture(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["raw"] = request.data
        return io.BytesIO(b"\x89PNG fake-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


class TestReferenceB64Payload:
    def test_a_without_reference_payload_is_byte_identical(self, monkeypatch):
        """(A) reference_b64 omitted -> the raw request body is exactly the
        current 6-key JSON (regression pin: byte-identical legacy payload)."""
        captured = _capture(monkeypatch)
        HttpFluxTransport(_config()).generate("p", 1, "8x8")
        expected = json.dumps({
            "prompt": "p", "seed": 1, "size": "8x8",
            "model": "flux.1-schnell", "lora": "vl1-style", "negative": "",
        }).encode()
        assert captured["raw"] == expected, (
            f"payload without a reference must stay byte-identical; got "
            f"{captured['raw']!r}")
        data = json.loads(captured["raw"])
        assert sorted(data) == sorted(LEGACY_KEYS), sorted(data)
        assert "reference_b64" not in data

    def test_b_with_reference_payload_carries_the_field(self, monkeypatch):
        """(B) reference_b64 set -> the JSON payload includes the key with
        the exact value (and only that key is added)."""
        captured = _capture(monkeypatch)
        HttpFluxTransport(_config()).generate(
            "p", 1, "8x8", reference_b64=REF_B64)
        data = json.loads(captured["raw"])
        assert "reference_b64" in data, (
            f"payload must carry reference_b64; keys={sorted(data)}")
        assert data["reference_b64"] == REF_B64
        assert sorted(data) == sorted(LEGACY_KEYS + ["reference_b64"]), (
            sorted(data))

    def test_c_signatures_declare_reference_b64_default_none(self):
        """(C) both the Protocol and the HTTP implementation declare a
        trailing keyword ``reference_b64=None`` (optional — the default
        keeps the legacy payload byte-identical)."""
        for target in (FluxTransport.generate, HttpFluxTransport.generate):
            params = inspect.signature(target).parameters
            param = params.get("reference_b64")
            assert param is not None, (
                f"{target.__qualname__} must declare reference_b64; "
                f"params={list(params)}")
            assert param.default is None, (
                f"{target.__qualname__}.reference_b64 must default to None, "
                f"got {param.default!r}")
