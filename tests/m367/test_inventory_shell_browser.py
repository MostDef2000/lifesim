"""#367: real Chromium component acceptance with production JS excerpts and full CSS.

No backend or external network. Chrome is optional for developer environments;
the existing Node/pytest tests remain the baseline when it is not installed.
"""
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "backend/app/web"
HERE = Path(__file__).parent


def _between(source, start, end):
    i = source.index(start)
    return source[i:source.index(end, i + 1)]


def _wait_for_port(port_file, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Chromium exited before DevTools was ready")
        if port_file.exists():
            lines = port_file.read_text().splitlines()
            if lines:
                return int(lines[0])
        time.sleep(0.05)
    raise AssertionError("Chromium DevTools port unavailable")


def _cdp(sock, method, params=None):
    sock.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
    while True:
        reply = json.loads(sock.recv(timeout=15))
        if reply.get("id") == 1:
            assert "error" not in reply, reply
            return reply.get("result", {})


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_inventory_shell_in_chromium(tmp_path, width, height):
    chrome = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    if not chrome:
        pytest.skip("Chromium unavailable; real browser acceptance still required")

    js = (WEB / "app.js").read_text(encoding="utf-8")
    parts = [
        _between(js, "function appendKids(", "/* ---------- routing"),
        _between(js, "const RETRY_HINT", "/* ---------- #306"),
        _between(js, "function svgIcon(", "/* ---------- world view"),
        _between(js, "const INV_WEARABLE_TYPES", "/* ---------- #65: «Образ и одежда»"),
    ]
    fixture = (HERE / "inventory_shell.browser-fixture.js").read_text(encoding="utf-8")
    (tmp_path / "probe.js").write_text(
        fixture + "\n".join(parts)
        + "\nprobe().then(r => window.__report = r,"
        + " e => window.__report = {ok:false,error:String(e.stack || e)});",
        encoding="utf-8",
    )
    shutil.copyfile(WEB / "style.css", tmp_path / "style.css")
    page = tmp_path / "index.html"
    page.write_text(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="style.css"></head>'
        '<body><div id="app"></div><script src="probe.js"></script></body></html>',
        encoding="utf-8",
    )
    profile = tmp_path / "chrome"
    process = subprocess.Popen(
        [
            chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--disable-background-networking", "--no-first-run",
            "--no-default-browser-check", "--remote-debugging-port=0",
            f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = _wait_for_port(profile / "DevToolsActivePort", process)
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
            targets = json.load(response)
        target = next(t for t in targets if t["type"] == "page")
        with connect(target["webSocketDebuggerUrl"], open_timeout=5) as sock:
            _cdp(sock, "Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": False,
            })
            _cdp(sock, "Page.navigate", {"url": page.as_uri() + "#/inventory"})
            deadline = time.monotonic() + 15
            report = None
            while time.monotonic() < deadline:
                result = _cdp(sock, "Runtime.evaluate", {
                    "expression": "window.__report || null", "returnByValue": True,
                })
                report = result.get("result", {}).get("value")
                if report:
                    break
                time.sleep(0.05)
            assert report and report.get("ok"), report
            assert (report["width"], report["height"]) == (width, height), report
            png = _cdp(sock, "Page.captureScreenshot", {"format": "png"})
            (tmp_path / f"inventory-{width}.png").write_bytes(base64.b64decode(png["data"]))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
