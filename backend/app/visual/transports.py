"""M6 T4 (SPEC §66/§15): Flux transports.

Protocol: generate(prompt, seed, size) -> PNG bytes.
- StubFluxTransport: deterministic CI-safe placeholder (no network).
- HttpFluxTransport: POST {base_url}/generate against the external Flux server (§15).
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from typing import Protocol

from app.config.config import VisualConfig


class FluxTransport(Protocol):
    def generate(self, prompt: str, seed: int, size: str) -> bytes:
        ...


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _render_placeholder_png(prompt: str, seed: int, size: str) -> bytes:
    """Minimal valid PNG whose pixels are a pure function of (prompt, seed, size).

    Determinism (AE3''''): identical inputs -> identical bytes.
    """
    w, h = (int(v) for v in size.lower().split("x"))
    w = max(1, min(w, 2048))
    h = max(1, min(h, 2048))
    # Pixel pattern derived from hash chain of prompt+seed.
    base = hashlib.sha256(f"{seed}:{prompt}".encode()).digest()
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")  # filter: none
        for x in range(w):
            idx = (x * 3 + y * 7) % len(base)
            row.append(base[idx])
            row.append(base[(idx + 11) % len(base)])
            row.append(base[(idx + 23) % len(base)])
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _png_chunk(b"IEND", b"")
    )


class StubFluxTransport:
    """CI-safe visualizer stand-in: pure function, no network, deterministic."""

    def generate(self, prompt: str, seed: int, size: str) -> bytes:
        return _render_placeholder_png(prompt, seed, size)


class HttpFluxTransport:
    """Client of the external Flux+LoRA server (§15). Never makes game decisions."""

    def __init__(self, config: VisualConfig):
        self._config = config

    def generate(self, prompt: str, seed: int, size: str) -> bytes:
        import urllib.error
        import urllib.request

        payload = json.dumps({
            "prompt": prompt,
            "seed": seed,
            "size": size,
            "model": self._config.model,
            "lora": self._config.lora,
        }).encode()
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_sec) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(f"flux unavailable: {exc}") from exc


def build_transport(config: VisualConfig) -> FluxTransport:
    if config.transport == "stub":
        return StubFluxTransport()
    if config.transport == "http":
        return HttpFluxTransport(config)
    raise ValueError(f"unknown visual transport: {config.transport}")
