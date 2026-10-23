"""020 P3 stage 2 — adapter reference consumption (flux-kontext graph).

Contract under test (owner-locked stage-2 scope, deploy/comfy_adapter.py):
- POST /generate gains an OPTIONAL `reference_b64` field (base64 of PNG
  bytes). ABSENT -> the built graph is byte-identical to the legacy txt2img
  graph (regression pin: dict equality against build_graph for the same
  args, node ids "1".."8", no kontext nodes). EMPTY ("") -> same legacy graph.
- reference_b64 + model="flux" -> kontext graph:
  * diffusion-model checkpoint flux1-dev-kontext_fp8_scaled.safetensors
    loaded through a UNET/diffusion loader — NEVER CheckpointLoaderSimple
    (kontext is a diffusion_models checkpoint, not a Checkpoint one);
  * LoadImage fed from a base64 data URI carrying the exact reference bytes;
  * FluxGuidance guidance 3.5;
  * explicit denoise on the sampler path (img2img off the reference latent);
  * VAE from ae, CLIPLoader present, SaveImage present;
  * no EmptyLatentImage (the reference latent seeds the sample, not a blank).
- reference_b64 + model="pony" -> HTTP 400 (kontext is flux-only; pony
  img2img is out of scope this wave).
- Malformed base64 in reference_b64 -> HTTP 400.

Import/test pattern follows tests/m19/test_comfy_adapter.py (the stdlib
adapter module is imported straight from deploy/). Fully offline: the
ComfyUI side is monkeypatched (comfy_post/comfy_history/comfy_view) — only
the real adapter HTTP Handler runs, on a loopback port.

RED discipline: on base 0a2a25e the handler never reads `reference_b64`
(legacy graph for every payload) -> the kontext assertions fail and the
pony/malformed cases answer 200 instead of 400. The absent/empty legacy
pins pass BY DESIGN on base — they are regression guards that must stay
green after the change.
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
# Broken padding in a pure-base64-alphabet string: binascii.Error in EVERY
# decode mode (validate=True and validate=False) — nothing lenient can
# salvage it, so any "malformed -> 400" implementation must reject it.
MALFORMED_B64 = "AAAAA"
PNG_OUT = PNG_MAGIC + b"generated-image"

BASE_PAYLOAD = {
    "prompt": "Portrait of Anna", "seed": 42, "size": "512x512",
    "model": "flux", "lora": "",
}
LEGACY_NODE_IDS = {"1", "2", "3", "4", "5", "6", "7", "8"}
KONTEXT_CKPT = "flux1-dev-kontext_fp8_scaled.safetensors"


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


def _legacy_flux_graph():
    return comfy_adapter.build_graph(
        model_key="flux", prompt=BASE_PAYLOAD["prompt"], seed=42,
        width=512, height=512, lora="")


class TestReferenceAbsentKeepsLegacyGraph:
    def test_a_no_reference_field_is_the_legacy_graph(self, adapter):
        """(A) ABSENT field -> the exact legacy txt2img graph (byte-identical
        to build_graph for the same args; node ids 1..8; no kontext nodes)."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD))
        assert status == 200, body
        graph = sink["graph"]
        assert graph == _legacy_flux_graph(), (
            "a payload without reference_b64 must build the EXACT legacy "
            "graph; node-level diff follows")
        assert set(graph) == LEGACY_NODE_IDS, sorted(graph)
        assert graph["1"]["class_type"] == "CheckpointLoaderSimple"
        assert graph["1"]["inputs"]["ckpt_name"] == "flux1-dev-fp8.safetensors"
        assert graph["8"]["class_type"] == "FluxGuidance"
        assert graph["8"]["inputs"]["guidance"] == 3.5
        class_types = {node["class_type"] for node in graph.values()}
        assert "LoadImage" not in class_types, class_types
        assert not any("unet_name" in node["inputs"]
                       for node in graph.values()), (
            "kontext diffusion loader leaked into the legacy path")
        assert not any("vae_name" in node["inputs"]
                       for node in graph.values())

    def test_b_empty_reference_b64_is_the_legacy_graph(self, adapter):
        """(B) EMPTY field -> same legacy graph (empty == absent)."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD, reference_b64=""))
        assert status == 200, body
        graph = sink["graph"]
        assert graph == _legacy_flux_graph(), (
            'reference_b64="" must behave exactly like an absent field')
        assert set(graph) == LEGACY_NODE_IDS, sorted(graph)


class TestKontextGraph:
    def test_c_flux_with_reference_builds_the_kontext_graph(self, adapter):
        """(C) reference_b64 + model=flux -> the ComfyUI-native kontext
        workflow shape (diffusion loader, LoadImage data URI, FluxGuidance
        3.5, explicit denoise, ae VAE, CLIPLoader, SaveImage)."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        nodes = list(graph.values())
        class_types = [node["class_type"] for node in nodes]

        # (1) diffusion-model checkpoint via a UNET/diffusion loader,
        #     never CheckpointLoaderSimple (kontext is diffusion_models).
        loaders = [node for node in nodes
                   if "unet_name" in node["inputs"]
                   or node["class_type"] in ("UNETLoader",
                                             "DiffusionModelLoader")]
        assert loaders, (
            f"no diffusion-model loader node; class_types={class_types}")
        assert loaders[0]["inputs"]["unet_name"] == KONTEXT_CKPT, (
            f"kontext checkpoint must be {KONTEXT_CKPT!r}; got "
            f"{loaders[0]['inputs'].get('unet_name')!r}")
        assert "CheckpointLoaderSimple" not in class_types, (
            "kontext must NOT load through CheckpointLoaderSimple")

        # (2) LoadImage fed from a base64 data URI carrying the reference
        #     bytes exactly as uploaded.
        load_images = [node for node in nodes
                       if node["class_type"] == "LoadImage"]
        assert load_images, (
            f"no LoadImage node; class_types={class_types}")
        decoded = []
        for node in load_images:
            image = node["inputs"].get("image")
            if not isinstance(image, str):
                continue
            header, _, b64data = image.partition(",")
            if header.startswith("data:image") and "base64" in header:
                decoded.append(base64.b64decode(b64data))
        assert decoded, (
            "LoadImage must be fed a base64 data URI; got "
            f"{[node['inputs'].get('image') for node in load_images]!r}")
        assert REF_PNG in decoded, (
            "the data URI must decode to the exact reference PNG bytes")

        # (3) FluxGuidance pinned at 3.5 (guidance-distilled flux path).
        flux_guidance = [node for node in nodes
                         if node["class_type"] == "FluxGuidance"]
        assert flux_guidance, (
            f"no FluxGuidance node; class_types={class_types}")
        assert any(node["inputs"].get("guidance") == 3.5
                   for node in flux_guidance), (
            f"guidance must be 3.5; got "
            f"{[node['inputs'].get('guidance') for node in flux_guidance]}")

        # (4) denoise is EXPLICIT on the sampling path (no default hidden).
        denoise_nodes = [node for node in nodes
                         if "denoise" in node["inputs"]]
        assert denoise_nodes, "denoise must be explicit in the kontext graph"
        assert all(isinstance(node["inputs"]["denoise"], (int, float))
                   for node in denoise_nodes), (
            f"denoise must be numeric; got "
            f"{[node['inputs']['denoise'] for node in denoise_nodes]}")

        # (5) VAE from ae + a CLIP loader + SaveImage output.
        assert any("ae" in str(node["inputs"].get("vae_name", ""))
                   for node in nodes), (
            f"the VAE must load ae; got "
            f"{[node['inputs'].get('vae_name') for node in nodes]}")
        assert any(node["class_type"].startswith("CLIPLoader")
                   or any(key.startswith("clip_name")
                          for key in node["inputs"])
                   for node in nodes), "no CLIP loader node"
        assert any(node["class_type"] == "SaveImage"
                   for node in nodes), (
            f"no SaveImage node; class_types={class_types}")

        # (6) the reference latent seeds the sample — no txt2img blank canvas.
        assert "EmptyLatentImage" not in class_types, class_types

        # (7) the positive prompt still reaches the graph.
        assert any(BASE_PAYLOAD["prompt"] in str(node["inputs"].get("text", ""))
                   for node in nodes), (
            "the positive prompt must be encoded somewhere in the graph")

    def test_d_pony_with_reference_is_400(self, adapter):
        """(D) pony + reference_b64 -> HTTP 400: kontext is flux-only and
        pony img2img is out of scope this wave."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD, model="pony",
                                          reference_b64=REF_B64))
        assert status == 400, (
            f"pony + reference_b64 must be 400 (flux-only kontext); got "
            f"{status}: {body[:200]!r}")
        assert "graph" not in sink, (
            "a rejected payload must never reach ComfyUI")

    def test_e_malformed_reference_b64_is_400(self, adapter):
        """(E) malformed base64 in reference_b64 -> HTTP 400, no submission."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=MALFORMED_B64))
        assert status == 400, (
            f"malformed reference_b64 must be 400; got "
            f"{status}: {body[:200]!r}")
        assert "graph" not in sink, (
            "a rejected payload must never reach ComfyUI")
