"""Phase 6 (issue #39): ComfyUI adapter contract tests.

Fake ComfyUI on a local port; adapter must speak the HttpFluxTransport
contract (POST /generate {prompt, seed, size, model, lora} -> PNG bytes)
and build a correct API-format workflow graph.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../deploy"))

import comfy_adapter  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FAKE_PNG = PNG_MAGIC + b"fakeimagebytes"


class FakeComfy(BaseHTTPRequestHandler):
    last_prompt = None
    history_served = False

    def log_message(self, format, *args):  # noqa: A002
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        assert self.path == "/prompt"
        length = int(self.headers.get("Content-Length", "0"))
        FakeComfy.last_prompt = json.loads(self.rfile.read(length))
        body = json.dumps({"prompt_id": "job-1"}).encode()
        self._send(200, body)

    def do_GET(self):
        if self.path == "/history/job-1":
            if FakeComfy.history_served:
                self._send(200, json.dumps({}).encode())
                return
            FakeComfy.history_served = True
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
            assert "filename=lifesim_00001_.png" in self.path
            self._send(200, FAKE_PNG, ctype="image/png")
        else:
            self._send(404, b"{}", "application/json")


def make_adapter_client():
    """Real adapter Handler against the fake ComfyUI backend."""
    FakeComfy.last_prompt = None
    FakeComfy.history_served = False
    comfy = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
    threading.Thread(target=comfy.serve_forever, daemon=True).start()
    comfy_adapter.COMFY_URL = f"http://127.0.0.1:{comfy.server_address[1]}"
    comfy_adapter.POLL_INTERVAL_SEC = 0.01
    adapter = ThreadingHTTPServer(("127.0.0.1", 0), comfy_adapter.Handler)
    threading.Thread(target=adapter.serve_forever, daemon=True).start()
    import http.client

    port = adapter.server_address[1]

    def post(payload):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/generate", json.dumps(payload),
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, response.read()

    return post


class TestAdapterContract:
    def test_flux_roundtrip_and_graph(self):
        post = make_adapter_client()
        status, body = post({
            "prompt": "Portrait of Anna", "seed": 42, "size": "512x512",
            "model": "flux", "lora": "",
        })
        assert status == 200 and body.startswith(PNG_MAGIC)
        graph = FakeComfy.last_prompt["prompt"]
        assert graph["1"]["inputs"]["ckpt_name"] == "flux1-dev-fp8.safetensors"
        assert graph["5"]["inputs"]["seed"] == 42
        assert graph["4"]["inputs"]["width"] == 512
        assert graph["4"]["inputs"]["height"] == 512
        # no LoRA node when lora is empty
        assert "0" not in graph
        # flux prompt passes through unchanged
        assert graph["2"]["inputs"]["text"] == "Portrait of Anna"
        assert FakeComfy.last_prompt["client_id"] == "lifesim-adapter"

    def test_pony_tags_and_checkpoint(self):
        post = make_adapter_client()
        status, body = post({
            "prompt": "1girl, dress", "seed": 7, "size": "1024x1024",
            "model": "pony", "lora": "",
        })
        assert status == 200 and body.startswith(PNG_MAGIC)
        graph = FakeComfy.last_prompt["prompt"]
        assert graph["1"]["inputs"]["ckpt_name"] == \
            "ponyDiffusionV6XL.safetensors"
        assert graph["2"]["inputs"]["text"].startswith(
            comfy_adapter.PONY_POSITIVE_PREFIX)
        assert graph["3"]["inputs"]["text"].startswith(
            comfy_adapter.PONY_NEGATIVE_PREFIX)

    def test_lora_node_wiring(self):
        post = make_adapter_client()
        status, _ = post({
            "prompt": "p", "seed": 1, "size": "512x512",
            "model": "flux", "lora": "flux1-uncensored.safetensors",
        })
        assert status == 200
        graph = FakeComfy.last_prompt["prompt"]
        assert graph["0"]["inputs"]["lora_name"] == \
            "flux1-uncensored.safetensors"
        # sampler and both encodes rewired through the LoRA node
        assert graph["5"]["inputs"]["model"] == ["0", 0]
        assert graph["2"]["inputs"]["clip"] == ["0", 1]
        assert graph["3"]["inputs"]["clip"] == ["0", 1]

    def test_unknown_model_422(self):
        post = make_adapter_client()
        status, body = post({
            "prompt": "p", "seed": 1, "size": "512x512",
            "model": "sdx999", "lora": "",
        })
        assert status == 422
        assert "unknown model" in json.loads(body)["error"]

    def test_bad_size_422(self):
        post = make_adapter_client()
        status, body = post({
            "prompt": "p", "seed": 1, "size": "giant", "model": "flux",
            "lora": "",
        })
        assert status == 422
        assert "bad size" in json.loads(body)["error"]

    def test_unknown_lora_skipped(self):
        post = make_adapter_client()
        status, _ = post({
            "prompt": "p", "seed": 1, "size": "512x512",
            "model": "flux", "lora": "evil.safetensors",
        })
        assert status == 200
        assert "0" not in FakeComfy.last_prompt["prompt"]

    def test_health(self):
        comfy = ThreadingHTTPServer(("127.0.0.1", 0), FakeComfy)
        threading.Thread(target=comfy.serve_forever, daemon=True).start()
        comfy_adapter.COMFY_URL = f"http://127.0.0.1:{comfy.server_address[1]}"
        adapter = ThreadingHTTPServer(("127.0.0.1", 0), comfy_adapter.Handler)
        threading.Thread(target=adapter.serve_forever, daemon=True).start()
        import http.client

        conn = http.client.HTTPConnection(
            "127.0.0.1", adapter.server_address[1], timeout=10)
        conn.request("GET", "/health")
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok"}
