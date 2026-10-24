#!/usr/bin/env python3
"""ComfyUI adapter for the VL1 visual pipeline (issue #39, phase 6).

Sits on the GPU machine between the app (HttpFluxTransport, SPEC §66-70)
and ComfyUI:

    app (VPS) --tunnel--> adapter :7860 --HTTP--> ComfyUI :8188

Contract (byte-compatible with backend/app/visual/transports.py):
    POST /generate  {"prompt", "seed", "size", "model", "lora", "negative"}
    -> PNG bytes

"negative" (#227 Phase 2b) is the composed app negative — APPENDED to the
adapter's technical floor in build_graph, never a replacement. Pre-update
adapters ignore the unknown key (data.get pattern), so old payloads keep
byte-identical behavior.

"reference_b64" (020 P3 stage 2) is an OPTIONAL base64 of a reference
image. ABSENT or empty -> the legacy txt2img graph, byte-identical to
build_graph (node ids "1".."8"). Non-empty + flux model -> a FLUX.1
Kontext Dev img2img graph (build_kontext_graph, the ComfyUI-native API
workflow: docs.comfy.org/tutorials/flux/flux-1-kontext-dev); the decoded
reference bytes are UPLOADED to ComfyUI's POST /upload/image (native
ingestion — LoadImage takes a file name from the input directory) before
any job is queued. Non-empty + any other model -> HTTP 400 (kontext is
flux-only; pony img2img is out of scope). Malformed base64 -> HTTP 400.
Upload failure -> the comfy-unavailable 502.

The app never speaks ComfyUI's queue API; the adapter owns the workflow
graph, model routing (flux/pony) and the Pony booru-style prompt tag.
Kept dependency-free (stdlib) so it runs anywhere on the worker.
"""
import base64
import binascii
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
LISTEN_PORT = int(os.getenv("ADAPTER_PORT", "7860"))
POLL_INTERVAL_SEC = float(os.getenv("ADAPTER_POLL_SEC", "0.5"))
POLL_TIMEOUT_SEC = float(os.getenv("ADAPTER_TIMEOUT_SEC", "180"))
CLIENT_ID = "lifesim-adapter"

# model alias -> ComfyUI checkpoint (probe 2026-09-20, worker 10.123.239.102)
MODEL_MAP = {
    "flux": "flux1-dev-fp8.safetensors",
    "flux1-dev-fp8.safetensors": "flux1-dev-fp8.safetensors",
    "pony": "ponyDiffusionV6XL.safetensors",
    "ponydiffusionv6xl.safetensors": "ponyDiffusionV6XL.safetensors",
}
KNOWN_LORAS = {"flux1-uncensored.safetensors"}

NEGATIVE = "lowres, bad anatomy, bad hands, watermark, blurry"
PONY_POSITIVE_PREFIX = "score_9, score_8, score_7, "
PONY_NEGATIVE_PREFIX = "score_6, score_5, score_4, "

# 020 P3 stage 2: FLUX.1 Kontext Dev (img2img off an uploaded reference).
# The kontext checkpoint is a diffusion_models checkpoint — it loads through
# UNETLoader, never CheckpointLoaderSimple. Text uses the native flux pair
# through DualCLIPLoader's NATIVE inputs (clip_name1=clip_l first,
# clip_name2=t5xxl scaled second, type="flux"); the VAE is ae.
KONTEXT_UNET = "flux1-dev-kontext_fp8_scaled.safetensors"
KONTEXT_CLIP_T5 = "t5xxl_fp8_e4m3fn_scaled.safetensors"
KONTEXT_CLIP_L = "clip_l.safetensors"
KONTEXT_VAE = "ae.safetensors"
FLUX_CHECKPOINT = "flux1-dev-fp8.safetensors"


class BadReference(ValueError):
    """reference_b64 problem -> HTTP 400 (kontext is flux-only; malformed
    base64). Subclasses ValueError so legacy except-clauses keep matching;
    the Handler answers it BEFORE the generic 422 branch."""


def resolve_model(name: str) -> str:
    ckpt = MODEL_MAP.get((name or "").strip().lower())
    if ckpt is None:
        raise ValueError(f"unknown model: {name!r}")
    return ckpt


def parse_size(size: str) -> tuple:
    try:
        w, h = (int(part) for part in size.lower().split("x", 1))
        if w <= 0 or h <= 0 or w > 2048 or h > 2048:
            raise ValueError
        return w, h
    except (ValueError, AttributeError):
        raise ValueError(f"bad size: {size!r}")


def build_positive(model_key: str, prompt: str) -> str:
    if model_key == "pony":
        return PONY_POSITIVE_PREFIX + prompt
    return prompt


def build_negative(model_key: str) -> str:
    if model_key == "pony":
        return PONY_NEGATIVE_PREFIX + NEGATIVE
    return NEGATIVE


def build_graph(model_key: str, prompt: str, seed: int, width: int,
                height: int, lora: str, negative: str = "") -> dict:
    """Minimal txt2img API-format graph. Node ids fixed for testability.

    #227 2b: the app-side negative AUGMENTS the adapter floor — it is
    appended to build_negative() and never replaces it. Payloads without
    the field keep the exact legacy floor (byte-identical behavior).
    """
    ckpt = resolve_model(model_key)
    # flux-dev is guidance-distilled: the guidance value lives in the
    # FluxGuidance node ("8") and KSampler cfg stays 1.0 — cfg>1 double-
    # applies guidance and makes portraits soft/oversaturated (#204).
    # Pony keeps the legacy cfg>1 sampler path unchanged.
    is_flux = ckpt == "flux1-dev-fp8.safetensors"
    effective_negative = build_negative(model_key) + (
        f", {negative}" if negative else "")
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": build_positive(model_key, prompt),
                         "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": effective_negative,
                         "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height,
                         "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": 28 if is_flux else 20,
                         "cfg": 1.0 if is_flux else 7.0,
                         "sampler_name": "euler", "scheduler": "normal",
                         "denoise": 1.0, "model": ["1", 0],
                         "positive": ["8", 0] if is_flux else ["2", 0],
                         "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "lifesim",
                         "images": ["6", 0]}},
    }
    if is_flux:
        # node "8": FluxGuidance between the positive encode and KSampler
        # (guidance-distilled flux-dev; id fixed for testability)
        graph["8"] = {"class_type": "FluxGuidance",
                      "inputs": {"guidance": 3.5, "conditioning": ["2", 0]}}
    model_out, clip_out = ["1", 0], ["1", 1]
    if lora and lora.strip().lower() in KNOWN_LORAS:
        graph["0"] = {"class_type": "LoraLoader",
                      "inputs": {"lora_name": lora.strip(),
                                 "strength_model": 1.0,
                                 "strength_clip": 1.0,
                                 "model": model_out, "clip": clip_out}}
        model_out, clip_out = ["0", 0], ["0", 1]
    graph["5"]["inputs"]["model"] = model_out
    graph["2"]["inputs"]["clip"] = clip_out
    graph["3"]["inputs"]["clip"] = clip_out
    return graph


def _upload_reference(reference_raw: bytes) -> str:
    """020 P3 stage 2 (revised contract): ingest the reference the NATIVE
    ComfyUI way — POST the decoded bytes to ComfyUI's POST /upload/image as
    a hand-rolled stdlib multipart body (file field "image", random unique
    filename by magic bytes, overwrite=true as a multipart form field —
    upstream server.py store_image_upload reads post.get("overwrite") from
    the form, not the query) and return
    the stored file NAME the server reports. Native class LoadImage takes a
    FILE NAME from ComfyUI's input directory (upstream VALIDATE_INPUTS
    checks existence) and does NOT decode data URIs.

    Called BEFORE any graph is submitted: upload failure (non-200, network
    error, or a 200 response without a "name") raises the same comfy-
    unavailable error the Handler maps to 502, so no job ever reaches
    /prompt with a dangling reference.
    """
    ext = "jpg" if reference_raw.startswith(b"\xff\xd8\xff") else "png"
    filename = f"ref_{uuid.uuid4().hex}.{ext}"
    boundary = f"----lifesim{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n'
        f"\r\n"
        f"true\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: image/{'jpeg' if ext == 'jpg' else 'png'}\r\n"
        f"\r\n"
    ).encode("ascii") + reference_raw + f"\r\n--{boundary}--\r\n".encode(
        "ascii")
    request = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=body,
        headers={"Content-Type":
                 f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.loads(response.read())
    except json.JSONDecodeError as exc:
        # json.JSONDecodeError is a ValueError, which the Handler maps to
        # 422 — a garbage upload response is a COMFY failure, not a bad
        # request; re-raise as the comfy-unavailable error (-> 502).
        raise RuntimeError(
            f"comfy unavailable: bad /upload/image reply: {exc}") from exc
    name = reply.get("name") if isinstance(reply, dict) else None
    if not name:
        raise RuntimeError(
            "comfy unavailable: /upload/image returned no name")
    return str(name)


def build_kontext_graph(prompt: str, seed: int, lora: str,
                        negative: str = "", reference_name: str = "") -> dict:
    """FLUX.1 Kontext Dev img2img graph (020 P3 stage 2) — the ComfyUI-native
    API workflow shape (docs.comfy.org/tutorials/flux/flux-1-kontext-dev):

    - the diffusion-model checkpoint loads through UNETLoader (kontext ships
      as diffusion_models; CheckpointLoaderSimple would fail);
    - text encodes through DualCLIPLoader with the NATIVE upstream inputs
      (upstream master nodes.py, class DualCLIPLoader): clip_name1=
      clip_l.safetensors, clip_name2=t5xxl_fp8_e4m3fn_scaled.safetensors,
      type="flux" — the flux recipe is clip-l first, t5 second;
    - the reference was UPLOADED beforehand (_upload_reference) and reaches
      LoadImage as the returned FILE NAME; it is normalized by
      FluxKontextImageScale and VAE-encoded; the encoded reference latent
      feeds BOTH ReferenceLatent (conditioning) and KSampler latent_image —
      there is NO EmptyLatentImage (the reference seeds the sample);
    - FluxGuidance 3.5 (guidance-distilled flux path, same convention as the
      txt2img graph) and an EXPLICIT denoise on the sampler.

    Node ids fixed for testability. Only called when reference_b64 is
    non-empty and the model resolved to flux (generate_png enforces both)
    and the upload has already succeeded. The composed app negative keeps
    the #227 augment semantics (floor + app negative, never a replacement).
    """
    effective_negative = build_negative("flux") + (
        f", {negative}" if negative else "")
    graph = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": KONTEXT_UNET,
                         "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": KONTEXT_CLIP_L,
                         "clip_name2": KONTEXT_CLIP_T5,
                         "type": "flux"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": KONTEXT_VAE}},
        "4": {"class_type": "LoadImage",
              "inputs": {"image": reference_name}},
        "5": {"class_type": "FluxKontextImageScale",
              "inputs": {"image": ["4", 0]}},
        "6": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["2", 0]}},
        "8": {"class_type": "CLIPTextEncode",
              "inputs": {"text": effective_negative, "clip": ["2", 0]}},
        "9": {"class_type": "FluxGuidance",
              "inputs": {"guidance": 3.5, "conditioning": ["7", 0]}},
        "10": {"class_type": "ReferenceLatent",
               "inputs": {"conditioning": ["9", 0], "latent": ["6", 0]}},
        "11": {"class_type": "KSampler",
               "inputs": {"seed": seed, "steps": 20, "cfg": 1.0,
                          "sampler_name": "euler", "scheduler": "simple",
                          "denoise": 1.0, "model": ["1", 0],
                          "positive": ["10", 0], "negative": ["8", 0],
                          "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode",
               "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "lifesim",
                          "images": ["12", 0]}},
    }
    model_out, clip_out = ["1", 0], ["2", 0]
    if lora and lora.strip().lower() in KNOWN_LORAS:
        graph["0"] = {"class_type": "LoraLoader",
                      "inputs": {"lora_name": lora.strip(),
                                 "strength_model": 1.0,
                                 "strength_clip": 1.0,
                                 "model": model_out, "clip": clip_out}}
        model_out, clip_out = ["0", 0], ["0", 1]
    graph["11"]["inputs"]["model"] = model_out
    graph["7"]["inputs"]["clip"] = clip_out
    graph["8"]["inputs"]["clip"] = clip_out
    return graph


def comfy_post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{COMFY_URL}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def comfy_history(prompt_id: str) -> dict:
    with urllib.request.urlopen(
            f"{COMFY_URL}/history/{prompt_id}", timeout=30) as response:
        return json.loads(response.read())


def comfy_view(filename: str, subfolder: str, folder_type: str) -> bytes:
    query = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": folder_type})
    with urllib.request.urlopen(
            f"{COMFY_URL}/view?{query}", timeout=60) as response:
        return response.read()


def _reference_graph_or_raise(model_key: str, prompt: str, seed: int,
                              lora: str, negative: str,
                              reference_b64: str) -> dict:
    """020 P3 stage 2: validate a reference request, UPload the reference
    and build the kontext graph. Raises BadReference (-> HTTP 400) when the
    model is not flux (kontext is flux-only; NO pony img2img path) or the
    base64 is malformed. Unknown models keep the plain ValueError -> 422.
    The upload (_upload_reference) runs BEFORE any graph submission: its
    failure raises the comfy-unavailable error the Handler maps to 502, so
    a broken upload can never reach /prompt."""
    ckpt = resolve_model(model_key)
    if ckpt != FLUX_CHECKPOINT:
        raise BadReference(
            f"reference_b64 requires model=flux, got {model_key!r}")
    try:
        reference_raw = base64.b64decode(reference_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BadReference(f"malformed reference_b64: {exc}") from exc
    if not reference_raw:
        raise BadReference("reference_b64 decodes to empty bytes")
    reference_name = _upload_reference(reference_raw)
    return build_kontext_graph(prompt, seed, lora, negative, reference_name)


def generate_png(model_key: str, prompt: str, seed: int, size: str,
                 lora: str, negative: str = "",
                 reference_b64: str = "") -> bytes:
    width, height = parse_size(size)
    if reference_b64:
        graph = _reference_graph_or_raise(
            model_key, prompt, seed, lora, negative, reference_b64)
    else:
        graph = build_graph(model_key, prompt, seed, width, height, lora,
                            negative)
    result = comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    prompt_id = result["prompt_id"]
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        history = comfy_history(prompt_id)
        entry = history.get(prompt_id)
        if entry is not None:
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(f"comfy job failed: {prompt_id}")
            images = [
                img
                for node_output in entry.get("outputs", {}).values()
                for img in node_output.get("images", [])
            ]
            if images:
                first = images[0]
                return comfy_view(first["filename"],
                                  first.get("subfolder", ""),
                                  first.get("type", "output"))
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"comfy job timed out: {prompt_id}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # keep stdout quiet under systemd

    def _json_error(self, code: int, message: str):
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json_error(404, "not found")

    def do_POST(self):
        if self.path != "/generate":
            self._json_error(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            # 020 P3 stage 2: reference_b64 rides ONLY when the payload
            # carries a non-empty value — absent/empty keep the exact
            # legacy positional call shape (older generate_png shims and
            # the #227 test spy take 6 args and no reference kwarg).
            # Absent and empty are treated identically (legacy graph).
            kwargs = {}
            reference_b64 = str(data.get("reference_b64", "") or "")
            if reference_b64:
                kwargs["reference_b64"] = reference_b64
            png = generate_png(
                str(data.get("model", "")),
                str(data.get("prompt", "")),
                int(data.get("seed", 0)),
                str(data.get("size", "512x512")),
                str(data.get("lora", "")),
                # #227 2b: the composed app negative (empty for old
                # payloads) — appended to the floor in build_graph.
                str(data.get("negative", "")),
                **kwargs,
            )
        except BadReference as exc:
            # 020 P3 stage 2: kontext is flux-only / malformed reference ->
            # 400 BEFORE anything reaches ComfyUI.
            self._json_error(400, str(exc))
            return
        except (ValueError, KeyError) as exc:
            self._json_error(422, str(exc))
            return
        except (urllib.error.URLError, TimeoutError, OSError,
                RuntimeError) as exc:
            self._json_error(502, f"comfy unavailable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.end_headers()
        self.wfile.write(png)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    print(f"comfy adapter listening on 127.0.0.1:{LISTEN_PORT} "
          f"-> {COMFY_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
