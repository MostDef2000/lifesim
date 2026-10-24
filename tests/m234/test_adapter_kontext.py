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
  * DualCLIPLoader with the NATIVE inputs of upstream class DualCLIPLoader:
    clip_name1="clip_l.safetensors",
    clip_name2="t5xxl_fp8_e4m3fn_scaled.safetensors", type="flux"
    — a bare `clip_name` key is the WRONG contract;
  * reference ingestion by UPLOAD: the adapter POSTs the decoded reference
    bytes to ComfyUI's POST /upload/image as multipart (field "image",
    random unique filename, overwrite=true) and LoadImage's `image` input
    is the name RETURNED by that call — native upstream class LoadImage
    takes a FILE NAME from the input directory (VALIDATE_INPUTS checks
    existence) and does NOT accept data URIs;
  * upload failure (non-200, or 200 without a name) -> the adapter's
    flux-unavailable error -> HTTP 502 to the client;
  * FluxGuidance guidance 3.5;
  * explicit denoise on the sampler path (img2img off the reference latent);
  * VAE from ae, SaveImage present;
  * no EmptyLatentImage (the reference latent seeds the sample, not a blank).
- reference_b64 + model="pony" -> HTTP 400 (kontext is flux-only; pony
  img2img is out of scope this wave).
- Malformed base64 in reference_b64 -> HTTP 400.

CONTRACT REVISION (m234 adapter pins, 2026-10-24): the previous pins in
this file encoded a WRONG contract — verified against upstream ComfyUI
master (raw nodes.py, class DualCLIPLoader and class LoadImage) — namely a
single `clip_name` key on DualCLIPLoader and a base64 DATA URI in
LoadImage's `image` input. Those assertions were REPLACED (this is a
contract revision, not a weakening) by the native DualCLIPLoader input pin
and the /upload/image upload-flow pins. Every OTHER pin in this file is
unchanged: legacy graph byte-identity, kontext graph shape (UNETLoader
unet_name, VAE ae, FluxGuidance 3.5, explicit denoise, SaveImage, no
EmptyLatentImage), pony+reference 400, malformed b64 400.

Import/test pattern follows tests/m19/test_comfy_adapter.py (the stdlib
adapter module is imported straight from deploy/; the ComfyUI side is a
loopback fake HTTP server covering /prompt, /upload/image, /history and
/view — fully offline, real adapter HTTP Handler only).

RED discipline (this revision, against the current implementation): the
upload block of test_c and every test in TestNativeComfyUIContract fail
for the RIGHT reasons — the loader still carries the single `clip_name`
key, the reference is still embedded as a data URI, /upload/image is never
called, and upload failures cannot surface as 502. The absent/empty legacy
pins and the pony/malformed 400 pins pass BY DESIGN — regression guards
that must stay green both now and after the fix.
"""
import base64
import json
import os
import re
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
# Native DualCLIPLoader inputs (upstream ComfyUI master, class
# DualCLIPLoader): TWO clip names + a type — never a single clip_name.
CLIP_NAME1 = "clip_l.safetensors"
CLIP_NAME2 = "t5xxl_fp8_e4m3fn_scaled.safetensors"
CLIP_TYPE = "flux"


def _make_fake_comfy(sink):
    """In-process ComfyUI double on a loopback port (m19 FakeComfy
    pattern), covering the WHOLE surface the adapter speaks: POST /prompt,
    POST /upload/image, GET /history/*, GET /view. Everything the adapter
    sends is observable at the socket, so no production function name is
    pinned — whatever verb/path the adapter uses for reference ingestion
    shows up here. sink["upload_response"] = (status, json) overrides the
    upload answer for the failure pins."""

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
                sink["upload_names"].append(
                    reply.get("name") if isinstance(reply, dict) else None)
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


def _multipart_filename(upload: dict):
    """The adapter-chosen filename inside the multipart file part."""
    match = re.search(rb'filename="([^"]*)"', upload["body"])
    return match.group(1).decode("latin-1") if match else None


def _overwrites(upload: dict) -> bool:
    """overwrite=true may ride the query string or a multipart form field."""
    if "overwrite=true" in upload["path"].lower():
        return True
    return bool(re.search(
        rb'name="overwrite"(?:\r\n[^\r\n]+)*\r\n\r\ntrue\r\n',
        upload["body"], re.IGNORECASE))


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
        workflow shape (diffusion loader, reference UPLOADED and named in
        LoadImage, FluxGuidance 3.5, explicit denoise, ae VAE,
        DualCLIPLoader, SaveImage)."""
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

        # (2) REFERENCE INGESTION — REVISED pin (was: "LoadImage fed from
        #     a base64 data URI carrying the exact reference bytes"): the
        #     adapter must POST the decoded reference bytes to ComfyUI's
        #     POST /upload/image as multipart (field "image", random
        #     filename, overwrite=true), and LoadImage's `image` input is
        #     the name RETURNED by that call — native LoadImage takes a
        #     FILE NAME from the input directory, it does not decode data
        #     URIs (upstream VALIDATE_INPUTS checks file existence).
        uploads = sink["uploads"]
        assert len(uploads) == 1, (
            "one reference generate -> exactly one POST /upload/image by "
            f"the adapter; got {len(uploads)}: "
            f"{[upload['path'] for upload in uploads]}")
        upload = uploads[0]
        assert upload["path"].partition("?")[0] == "/upload/image", (
            f"the reference must be uploaded to ComfyUI's /upload/image; "
            f"got {upload['path']!r}")
        assert upload["content_type"].startswith("multipart/form-data"), (
            f"the upload must be multipart/form-data; got "
            f"{upload['content_type']!r}")
        assert b'name="image"' in upload["body"], (
            'the multipart file part must be the field named "image"')
        assert REF_PNG in upload["body"], (
            "the multipart body must carry the EXACT reference bytes")
        assert _overwrites(upload), (
            f"the upload must set overwrite=true (query or form field); "
            f"got path={upload['path']!r}")
        load_images = [node for node in nodes
                       if node["class_type"] == "LoadImage"]
        assert load_images, (
            f"no LoadImage node; class_types={class_types}")
        returned = [name for name in sink["upload_names"] if name]
        assert returned, (
            "the /upload/image response must carry the stored file name")
        images = [node["inputs"].get("image") for node in load_images]
        assert images == [returned[-1]] * len(images), (
            f"LoadImage's image input must be the name RETURNED by "
            f"/upload/image (native LoadImage takes a FILE NAME); got "
            f"{images!r}, returned {returned[-1]!r}")

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


class TestNativeComfyUIContract:
    """REVISED pins against upstream ComfyUI master (raw nodes.py: class
    DualCLIPLoader, class LoadImage; plus the POST /upload/image
    ingestion). These REPLACE the old wrong-contract pins (single
    `clip_name` key, data-URI LoadImage) that used to live in this file —
    the graph-shape, legacy, pony-400 and malformed-400 pins above are
    untouched."""

    def test_dual_clip_loader_uses_the_native_flux_inputs(self, adapter):
        """Native class DualCLIPLoader requires clip_name1/clip_name2/type;
        the flux recipe is clip_l + t5xxl scaled, type "flux". The OLD
        single `clip_name` key this file used to pin is the WRONG
        contract — no bare clip_name key may exist anywhere in the graph."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        class_types = [node["class_type"] for node in graph.values()]
        loaders = [node for node in graph.values()
                   if node["class_type"] == "DualCLIPLoader"]
        assert loaders, f"no DualCLIPLoader node; class_types={class_types}"
        inputs = loaders[0]["inputs"]
        assert inputs.get("clip_name1") == CLIP_NAME1, (
            f"native DualCLIPLoader takes clip_name1={CLIP_NAME1!r}; got "
            f"{inputs.get('clip_name1')!r} (inputs={inputs!r})")
        assert inputs.get("clip_name2") == CLIP_NAME2, (
            f"native DualCLIPLoader takes clip_name2={CLIP_NAME2!r}; got "
            f"{inputs.get('clip_name2')!r} (inputs={inputs!r})")
        assert inputs.get("type") == CLIP_TYPE, (
            f"DualCLIPLoader type must be {CLIP_TYPE!r} for flux; got "
            f"{inputs.get('type')!r} (inputs={inputs!r})")
        for node_id, node in graph.items():
            assert "clip_name" not in node["inputs"], (
                f"the bare `clip_name` key is the WRONG contract (native "
                f"inputs are clip_name1/clip_name2); node {node_id} "
                f"({node['class_type']}) has inputs={node['inputs']!r}")

    def test_no_data_uri_in_any_node_input(self, adapter):
        """Native class LoadImage takes a FILE NAME from ComfyUI's input
        directory — a data URI is the wrong contract (REVISED pin: this
        file used to assert the data-URI shape). No node input may carry
        one."""
        server, sink = adapter
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 200, body
        graph = sink["graph"]
        for node_id, node in graph.items():
            for key, value in node["inputs"].items():
                if not isinstance(value, str):
                    continue
                assert not value.startswith("data:"), (
                    f"no data URI may ride the graph (native LoadImage "
                    f"takes the uploaded FILE NAME); node {node_id} "
                    f"({node['class_type']}) input {key}="
                    f"{value[:64]!r}...")

    def test_uploaded_filename_is_random_per_request(self, adapter):
        """The multipart file part must carry a filename, and it must be
        RANDOM (unique per request) — a fixed name would collide across
        concurrent generations even with overwrite=true."""
        server, sink = adapter
        for _ in range(2):
            status, body = _post(server, dict(BASE_PAYLOAD,
                                              reference_b64=REF_B64))
            assert status == 200, body
        assert len(sink["uploads"]) == 2, (
            "both generates must upload the reference; got "
            f"{len(sink['uploads'])}")
        names = [_multipart_filename(upload) for upload in sink["uploads"]]
        assert all(names), (
            f"the multipart file part must carry a filename; got {names!r}")
        assert names[0] != names[1], (
            "the upload filename must be RANDOM (unique per request); got "
            f"{names!r}")

    def test_upload_non_200_is_flux_unavailable_502(self, adapter):
        """upload/image answers non-200 -> the adapter's flux-unavailable
        error -> HTTP 502 to the client, and the job is never queued."""
        server, sink = adapter
        sink["upload_response"] = (500, {"error": "injected upload failure"})
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 502, (
            f"a failed /upload/image must surface as the adapter's "
            f"flux-unavailable 502; got {status}: {body[:200]!r}")
        error = json.loads(body).get("error", "")
        assert error, f"the 502 body must carry an error; got {body[:200]!r}"
        assert "graph" not in sink, (
            "a failed upload must never submit the job to ComfyUI")

    def test_upload_without_name_is_flux_unavailable_502(self, adapter):
        """upload/image answers 200 WITHOUT a name -> the adapter cannot
        name the LoadImage input -> flux-unavailable 502, job never queued."""
        server, sink = adapter
        sink["upload_response"] = (200, {"subfolder": "", "type": "input"})
        status, body = _post(server, dict(BASE_PAYLOAD,
                                          reference_b64=REF_B64))
        assert status == 502, (
            f"an upload response without a name must surface as the "
            f"adapter's flux-unavailable 502; got {status}: {body[:200]!r}")
        error = json.loads(body).get("error", "")
        assert error, f"the 502 body must carry an error; got {body[:200]!r}"
        assert "graph" not in sink, (
            "a failed upload must never submit the job to ComfyUI")
