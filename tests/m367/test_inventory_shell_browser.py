"""#367: real Chromium component acceptance with production JS excerpts and full CSS.

No backend or external network. Chrome is optional for developer environments;
the existing Node/pytest tests remain the baseline when it is not installed.
"""
import base64
import json
import shutil
import socket
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


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _devtools_ready(port_file, port):
    """DevTools is ready when the port file names a live port or the HTTP
    endpoint answers. The port file can appear late in CI (flake), so the
    HTTP /json/version probe is the authoritative fallback."""
    if port_file.exists():
        lines = port_file.read_text().splitlines()
        if lines and lines[0].strip().isdigit():
            return int(lines[0].strip())
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            if json.load(response).get("webSocketDebuggerUrl"):
                return port
    except (OSError, ValueError):
        pass
    return None


def _wait_for_port(port_file, process, port):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Chromium exited before DevTools was ready")
        ready = _devtools_ready(port_file, port)
        if ready is not None:
            return ready
        time.sleep(0.1)
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
    port = _free_port()
    process = subprocess.Popen(
        [
            chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--disable-background-networking", "--no-first-run",
            "--no-default-browser-check", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = _wait_for_port(profile / "DevToolsActivePort", process, port)
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
            # #367: real keyboard focus. Programmatic .focus() does not reliably
            # match :focus-visible in headless Chromium, so put focus on the dock
            # container and let a real Tab move it to the first destination.
            _cdp(sock, "Runtime.evaluate", {"expression": (
                "const nav = document.querySelector('.world-pill');"
                " nav.setAttribute('tabindex', '-1');"
                " nav.focus();"
            )})
            for event in ("rawKeyDown", "keyUp"):
                _cdp(sock, "Input.dispatchKeyEvent", {
                    "type": event, "key": "Tab", "code": "Tab",
                    "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9,
                })
            focus = _cdp(sock, "Runtime.evaluate", {
                "expression": (
                    "(() => { const first = document.querySelector('.world-pill a');"
                    " const s = getComputedStyle(first);"
                    " return {focused: document.activeElement === first,"
                    " active: document.activeElement && document.activeElement.tagName,"
                    " outlineStyle: s.outlineStyle, outlineWidth: s.outlineWidth}; })()"
                ),
                "returnByValue": True,
            })["result"]["value"]
            assert focus["focused"], focus
            assert focus["outlineStyle"] != "none" and focus["outlineWidth"] != "0px", focus
            png = _cdp(sock, "Page.captureScreenshot", {"format": "png"})
            (tmp_path / f"inventory-{width}.png").write_bytes(base64.b64decode(png["data"]))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
