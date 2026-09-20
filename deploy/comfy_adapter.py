#!/usr/bin/env python3
"""ComfyUI adapter for the VL1 visual pipeline (issue #39, phase 6).

Sits on the GPU machine between the app (HttpFluxTransport, SPEC §66-70)
and ComfyUI:

    app (VPS) --tunnel--> adapter :7860 --HTTP--> ComfyUI :8188

Contract (byte-compatible with backend/app/visual/transports.py):
    POST /generate  {"prompt", "seed", "size", "model", "lora"} -> PNG bytes

The app never speaks ComfyUI's queue API; the adapter owns the workflow
graph, model routing (flux/pony) and the Pony booru-style prompt tag.
Kept dependency-free (stdlib) so it runs anywhere on the worker.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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
KNOWN_LORAS = {"flux1-uncensored.safetensors", "valery23.safetensors"}

NEGATIVE = "lowres, bad anatomy, bad hands, watermark, blurry"
PONY_POSITIVE_PREFIX = "score_9, score_8, score_7, "
PONY_NEGATIVE_PREFIX = "score_6, score_5, score_4, "


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
                height: int, lora: str) -> dict:
    """Minimal txt2img API-format graph. Node ids fixed for testability."""
    ckpt = resolve_model(model_key)
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"text": build_positive(model_key, prompt),
                         "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": build_negative(model_key),
                         "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height,
                         "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": 20, "cfg": 7.0,
                         "sampler_name": "euler", "scheduler": "normal",
                         "denoise": 1.0, "model": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "lifesim",
                         "images": ["6", 0]}},
    }
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


def generate_png(model_key: str, prompt: str, seed: int, size: str,
                 lora: str) -> bytes:
    width, height = parse_size(size)
    graph = build_graph(model_key, prompt, seed, width, height, lora)
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
            png = generate_png(
                str(data.get("model", "")),
                str(data.get("prompt", "")),
                int(data.get("seed", 0)),
                str(data.get("size", "512x512")),
                str(data.get("lora", "")),
            )
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
