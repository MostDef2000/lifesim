"""020 P3 stage 2 — review hole T1: kontext LoraLoader rewiring (production lora).

Review hole T1: every existing kontext pin (tests/m234/test_adapter_kontext.py)
posts lora="" — the LoraLoader insertion and the model/CLIP rewiring through
node "0" in build_kontext_graph were never exercised. The PRODUCTION config
(config/production.yaml visual.lora) always sends
flux1-uncensored.safetensors, so the production reference path ALWAYS hits
that branch — a silent regression there (dropped rewiring, dangling lora
node, CheckpointLoaderSimple creeping back) would ship unnoticed.

Pins (adapter Handler pattern from test_adapter_kontext.py, fully offline —
ComfyUI served by a loopback fake HTTP double covering /prompt,
/upload/image, /history and /view, loopback client HTTP only):
- POST /generate {model: "flux", lora: flux1-uncensored.safetensors,
  reference_b64: ...} -> the kontext graph with the LoraLoader node "0"
  correctly rewired: KSampler takes the lora'd model ["0", 0], BOTH
  CLIPTextEncode nodes take the lora'd clip ["0", 1], the LoraLoader itself
  is fed straight from the UNETLoader/DualCLIPLoader (no dangling node), and
  CheckpointLoaderSimple never appears;
- lora="" keeps the DIRECT wiring (no node "0", KSampler model ["1", 0],
  clips ["2", 0]) — the rewiring is conditional, not unconditional;
- the clip source node "2" those wiring pins point at must be the NATIVE
  DualCLIPLoader (contract revision — see below): clip_name1/clip_name2/type,
  never a bare clip_name key;
- config/production.yaml visual.lora is exactly this lora (the pin's
  payload stays tied to what production actually sends; m21/m148 precedent
  for reading production.yaml in tests).

CONTRACT REVISION (2026-10-24): the wiring pins above are UNCHANGED — they
target node indices (["2", 0], ["0", 1], ["1", 0]) which the loader-input
revision does not move. What changed is what node "2" itself must satisfy:
upstream ComfyUI master (raw nodes.py, class DualCLIPLoader) requires
clip_name1="clip_l.safetensors", clip_name2="t5xxl_fp8_e4m3fn_scaled.safetensors",
type="flux" — the previously pinned single `clip_name` key is the WRONG
contract. That input pin is added via _assert_native_dual_clip, so the T1
wiring assertions are verified against the right wiring AFTER the loader
rename. The fake ComfyUI surface also answers POST /upload/image (the
native reference ingestion — native LoadImage takes a FILE NAME, not a
data URI), so the production reference payload is exercised offline end
to end.

RED discipline: on base dd7d11d (stage-1) the handler answers the legacy
txt2img graph for every payload — no UNETLoader, no node "0" — so the
wiring assertions fail. On the current (wrong-contract) implementation the
wiring pins PASS and the native DualCLIPLoader input pin fails (single
`clip_name` key still present) — the right failure for this revision. The
no-lora wiring assertions and the production-lora provenance pin pass by
design on every stage.
"""
import base64
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
# Native DualCLIPLoader inputs (upstream ComfyUI master, class
# DualCLIPLoader): TWO clip names + a type — never a single clip_name.
CLIP_NAME1 = "clip_l.safetensors"
CLIP_NAME2 = "t5xxl_fp8_e4m3fn_scaled.safetensors"
CLIP_TYPE = "flux"

BASE_PAYLOAD = {
    "prompt": "Portrait of Anna", "seed": 42, "size": "512x512",
    "model": "flux", "lora": "",
}


def _make_fake_comfy(sink):
    """In-process ComfyUI double on a loopback port (m19 FakeComfy
    pattern) covering /prompt, /upload/image, /history and /view — the
    whole surface the adapter speaks, so the native upload ingestion is
    answered offline and no production function name is pinned."""

    class FakeComfy(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            path = self.path.partition("?")[0]
            if path == "/prompt":
                payload = json.loads(raw)
                sink["graph"] = payload["prompt"]
                self._send(200, json.dumps({"prompt_id": "job-1"}).encode())
            elif path == "/upload/image":
                sink["uploads"].append({
                    "path": self.path,
                    "content_type": self.headers.get("Content-Type", ""),
                    "body": raw,
                })
                status, reply = sink.get("upload_response") or (
                    200,
                    {"name": f"ref_{uuid.uuid4().hex}.png",
                     "subfolder": "", "type": "input"},
                )
                self._send(status, json.dumps(reply).encode())
            else:
                self._send(404, b"{}", "application/json")

        def do_GET(self):
            if self.path.startswith("/history/"):
                body = json.dumps({
                    "job-1": {
                        "status": {"status_str": "success"},
                        "outputs": {"7": {"images": [{
                            "filename": "lifesim_00001_.png",
                            "subfolder": "", "type": "output",
                        }]}},
                    }
                }).encode()
                self._send(200, body)
            elif self.path.startswith("/view?"):
                self._send(200, PNG_OUT, ctype="image/png")
            else:
                self._send(404, b"{}", "application/json")

    return FakeComfy


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
    """Real adapter Handler + fake ComfyUI HTTP backend (m19 pattern,
    offline): /prompt, /upload/image, /history and /view are all served by
    the loopback double, and COMFY_URL/POLL_INTERVAL are pointed at it for
    the duration of the test (monkeypatch restores them)."""
    sink = {"uploads": [], "upload_names": []}
    comfy = ThreadingHTTPServer(("127.0.0.1", 0), _make_fake_comfy(sink))
    threading.Thread(target=comfy.serve_forever, daemon=True).start()
    monkeypatch.setattr(comfy_adapter, "COMFY_URL",
                        f"http://127.0.0.1:{comfy.server_address[1]}")
    monkeypatch.setattr(comfy_adapter, "POLL_INTERVAL_SEC", 0.01)
    server = ThreadingHTTPServer(("127.0.0.1", 0), comfy_adapter.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, sink
    finally:
        server.shutdown()
        comfy.shutdown()


def _assert_native_dual_clip(graph, anchor):
    """Node "2" is what the T1 wiring pins point at — it must be the NATIVE
    DualCLIPLoader (upstream class DualCLIPLoader: clip_name1/clip_name2/
    type), never the wrong single `clip_name` contract. Called from both
    the lora and the no-lora wiring pins so the rename cannot slip past
    either branch."""
    node = graph.get("2")
    assert node is not None and node["class_type"] == "DualCLIPLoader", (
        f"the clip source of {anchor} must be DualCLIPLoader at node '2'; "
        f"got {node!r}")
    inputs = node["inputs"]
    assert inputs.get("clip_name1") == CLIP_NAME1, (
        f"native DualCLIPLoader takes clip_name1={CLIP_NAME1!r} (anchor: "
        f"{anchor}); got {inputs.get('clip_name1')!r} "
        f"(inputs={inputs!r})")
    assert inputs.get("clip_name2") == CLIP_NAME2, (
        f"native DualCLIPLoader takes clip_name2={CLIP_NAME2!r} (anchor: "
        f"{anchor}); got {inputs.get('clip_name2')!r} "
        f"(inputs={inputs!r})")
    assert inputs.get("type") == CLIP_TYPE, (
        f"DualCLIPLoader type must be {CLIP_TYPE!r} for flux (anchor: "
        f"{anchor}); got {inputs.get('type')!r} (inputs={inputs!r})")
    assert "clip_name" not in inputs, (
        f"the bare `clip_name` key is the WRONG contract (native inputs "
        f"are clip_name1/clip_name2); node '2' has inputs={inputs!r}")


class TestKontextLoraRewiring:
    def test_a_production_lora_rewires_model_and_both_clips_through_node_0(
            self, adapter):
        """(T1) reference_b64 + model=flux + the PRODUCTION lora -> the
        kontext graph with LoraLoader node "0" IN THE CHAIN: KSampler and
        BOTH CLIP encoders consume node "0", the lora node is fed straight
        from the loaders, and CheckpointLoaderSimple never appears. The
        clip source chain is verified down to the native DualCLIPLoader
        inputs (contract revision)."""
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

        # the wiring targets node "2" — verify node "2" is the NATIVE
        # DualCLIPLoader (the loader rename did not move the wiring).
        _assert_native_dual_clip(graph,
                                 anchor="the lora'd clip chain ['0', 1]")

    def test_b_no_lora_keeps_the_direct_wiring(self, adapter):
        """(T1, reverse pin) lora="" -> NO node "0": KSampler keeps the
        direct UNETLoader link ["1", 0] and both CLIP encoders ["2", 0] —
        the rewiring must be conditional on a known lora, not unconditional.
        The clip source node "2" is verified as the native DualCLIPLoader
        (contract revision)."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        assert "0" not in graph, (
            f'lora="" must not insert a LoraLoader node; got {graph.get("0")!r}')
        assert graph["11"]["inputs"]["model"] == ["1", 0], graph["11"]
        assert graph["7"]["inputs"]["clip"] == ["2", 0], graph["7"]
        assert graph["8"]["inputs"]["clip"] == ["2", 0], graph["8"]
        _assert_native_dual_clip(graph,
                                 anchor="the direct clip wiring ['2', 0]")

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
