"""020 P3 stage 2 — review hole T1: kontext LoraLoader rewiring (production lora).

Review hole T1: every existing kontext pin (tests/m234/test_adapter_kontext.py)
posts lora="" — the LoraLoader insertion and the model/CLIP rewiring through
node "0" in build_kontext_graph were never exercised. The PRODUCTION config
(config/production.yaml visual.lora) always sends
flux1-uncensored.safetensors, so the production reference path ALWAYS hits
that branch — a silent regression there (dropped rewiring, dangling lora
node, CheckpointLoaderSimple creeping back) would ship unnoticed.

Pins (adapter Handler pattern from test_adapter_kontext.py, fully offline —
ComfyUI monkeypatched, loopback HTTP only):
- POST /generate {model: "flux", lora: flux1-uncensored.safetensors,
  reference_b64: ...} -> the kontext graph with the LoraLoader node "0"
  correctly rewired: KSampler takes the lora'd model ["0", 0], BOTH
  CLIPTextEncode nodes take the lora'd clip ["0", 1], the LoraLoader itself
  is fed straight from the UNETLoader/DualCLIPLoader (no dangling node), and
  CheckpointLoaderSimple never appears;
- lora="" keeps the DIRECT wiring (no node "0", KSampler model ["1", 0],
  clips ["2", 0]) — the rewiring is conditional, not unconditional;
- config/production.yaml visual.lora is exactly this lora (the pin's
  payload stays tied to what production actually sends; m21/m148 precedent
  for reading production.yaml in tests).

RED discipline: on base dd7d11d (stage-1) the handler answers the legacy
txt2img graph for every payload — no UNETLoader, no node "0" — so the
wiring assertions fail. The no-lora pin passes by design on every stage.
"""
import base64
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../deploy"))

import comfy_adapter  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
REF_PNG = PNG_MAGIC + b"reference-image-payload"
REF_B64 = base64.b64encode(REF_PNG).decode()
PNG_OUT = PNG_MAGIC + b"generated-image"

# The PRODUCTION lora (config/production.yaml visual.lora; also the sole
# member of the adapter's KNOWN_LORAS set) — the payload below is exactly
# what the production HttpFluxTransport sends on every reference request.
PROD_LORA = "flux1-uncensored.safetensors"
KONTEXT_CKPT = "flux1-dev-kontext_fp8_scaled.safetensors"

BASE_PAYLOAD = {
    "prompt": "Portrait of Anna", "seed": 42, "size": "512x512",
    "model": "flux", "lora": "",
}


def _fake_comfy(monkeypatch, sink):
    """In-process ComfyUI double: captures the submitted graph."""

    def fake_post(path, payload):
        sink["graph"] = payload["prompt"]
        return {"prompt_id": "job-1"}

    def fake_history(prompt_id):
        return {"job-1": {
            "status": {"status_str": "success"},
            "outputs": {"7": {"images": [{
                "filename": "lifesim_00001_.png",
                "subfolder": "", "type": "output",
            }]}},
        }}

    monkeypatch.setattr(comfy_adapter, "comfy_post", fake_post)
    monkeypatch.setattr(comfy_adapter, "comfy_history", fake_history)
    monkeypatch.setattr(comfy_adapter, "comfy_view",
                        lambda *a, **k: PNG_OUT)


def _post(server, payload):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1],
                                      timeout=10)
    conn.request("POST", "/generate", json.dumps(payload),
                 {"Content-Type": "application/json"})
    response = conn.getresponse()
    return response.status, response.read()


@pytest.fixture
def adapter(monkeypatch):
    """Real adapter Handler + fake ComfyUI backend (m19 pattern, offline)."""
    sink = {}
    _fake_comfy(monkeypatch, sink)
    server = ThreadingHTTPServer(("127.0.0.1", 0), comfy_adapter.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, sink
    finally:
        server.shutdown()


class TestKontextLoraRewiring:
    def test_a_production_lora_rewires_model_and_both_clips_through_node_0(
            self, adapter):
        """(T1) reference_b64 + model=flux + the PRODUCTION lora -> the
        kontext graph with LoraLoader node "0" IN THE CHAIN: KSampler and
        BOTH CLIP encoders consume node "0", the lora node is fed straight
        from the loaders, and CheckpointLoaderSimple never appears."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD, lora=PROD_LORA,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        class_types = {node["class_type"] for node in graph.values()}

        # the graph IS the kontext graph (diffusion loader, no checkpoint
        # loader) — the rewiring assertions below only make sense there.
        assert graph["1"]["class_type"] == "UNETLoader", (
            f"kontext must load through UNETLoader; got {graph['1']!r}")
        assert graph["1"]["inputs"]["unet_name"] == KONTEXT_CKPT
        assert "CheckpointLoaderSimple" not in class_types, (
            f"kontext must NEVER load through CheckpointLoaderSimple; "
            f"class_types={sorted(class_types)}")

        # node "0": the LoraLoader, fed straight from the real loaders
        # (a dangling node that nothing consumes is NOT a wiring).
        assert "0" in graph, (
            f"the production lora must insert a LoraLoader node; "
            f"nodes={sorted(graph)}")
        lora_node = graph["0"]
        assert lora_node["class_type"] == "LoraLoader", lora_node
        assert lora_node["inputs"]["lora_name"] == PROD_LORA, lora_node
        assert lora_node["inputs"]["model"] == ["1", 0], (
            f"LoraLoader.model must come from the UNETLoader; "
            f"got {lora_node['inputs']['model']!r}")
        assert lora_node["inputs"]["clip"] == ["2", 0], (
            f"LoraLoader.clip must come from the DualCLIPLoader; "
            f"got {lora_node['inputs']['clip']!r}")

        # every consumer takes the lora'd outputs through node "0".
        assert graph["11"]["inputs"]["model"] == ["0", 0], (
            f"KSampler must consume the lora'd model ['0', 0]; got "
            f"{graph['11']['inputs']['model']!r} (direct ['1', 0] means "
            f"the lora was loaded but never rewired)")
        assert graph["7"]["inputs"]["clip"] == ["0", 1], (
            f"the positive CLIPTextEncode must consume the lora'd clip "
            f"['0', 1]; got {graph['7']['inputs']['clip']!r}")
        assert graph["8"]["inputs"]["clip"] == ["0", 1], (
            f"the negative CLIPTextEncode must consume the lora'd clip "
            f"['0', 1]; got {graph['8']['inputs']['clip']!r}")

    def test_b_no_lora_keeps_the_direct_wiring(self, adapter):
        """(T1, reverse pin) lora="" -> NO node "0": KSampler keeps the
        direct UNETLoader link ["1", 0] and both CLIP encoders ["2", 0] —
        the rewiring must be conditional on a known lora, not unconditional."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        assert "0" not in graph, (
            f'lora="" must not insert a LoraLoader node; got {graph["0"]!r}')
        assert graph["11"]["inputs"]["model"] == ["1", 0], graph["11"]
        assert graph["7"]["inputs"]["clip"] == ["2", 0], graph["7"]
        assert graph["8"]["inputs"]["clip"] == ["2", 0], graph["8"]

    def test_c_production_config_sends_exactly_this_lora(self):
        """(T1, provenance) the payload pinned above is what production
        actually sends: config/production.yaml visual.lora is exactly the
        known lora the adapter rewires for (m21/m148 precedent: reading
        production.yaml from a test). If production ever changes the lora,
        this pin fails and forces a conscious update of T1."""
        from app.config.config import load_config

        prod = load_config("config/production.yaml")
        assert prod.visual.lora == PROD_LORA, (
            f"production visual.lora changed to {prod.visual.lora!r} — "
            f"the T1 wiring pin must be updated consciously")
        assert prod.visual.lora.strip().lower() in comfy_adapter.KNOWN_LORAS, (
            f"the production lora {prod.visual.lora!r} must stay in the "
            f"adapter's KNOWN_LORAS or the rewiring never fires")
