"""M6 T4 (SPEC §66/§15): Flux transports.

Protocol: generate(prompt, seed, size, negative="") -> PNG bytes.
- StubFluxTransport: deterministic CI-safe placeholder (no network).
- HttpFluxTransport: POST {base_url}/generate against the external Flux server (§15).

#227 Phase 2b: the trailing optional ``negative`` carries the composed
negative (descriptor.compose_negative) — the adapter appends it to its
technical floor; old payloads/adapters ignore the field (backward-compatible).

020 P3 stage 2: the trailing keyword-only ``reference_b64`` carries a
user-uploaded reference image (base64 of the original file bytes) for
kontext-style generation. None/empty -> the JSON payload is byte-identical
to the legacy one (the key is ABSENT, not empty — old adapters see exactly
the pre-stage-2 body); non-empty -> the adapter builds a flux-kontext graph
from it. The stub ignores the field (renderer stays a pure function of
prompt+seed+size).
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import socket
import struct
import zlib
from typing import Protocol

from app.config.config import VisualConfig

# #119 wave 3: same logger namespace as visual_routes.py — bounded transport
# lifecycle events (retry/recovered/exhausted) with attempt counts and error
# classification only; never prompt, reference bytes, request body, or
# credentials.
_LOGGER = logging.getLogger("vl1.visual")


class FluxTransport(Protocol):
    """#227 2b: implementations must declare a named ``negative: str = ""``
    parameter to receive the composed negative; legacy 3-arg transports are
    dispatched without it (see _transport_generate in visual_routes.py).

    020 P3 stage 2: implementations that accept references declare the
    keyword-only ``reference_b64`` (default None) — dispatched only when
    declared, same compat discipline."""

    def generate(
        self, prompt: str, seed: int, size: str, negative: str = "",
        *, reference_b64: str | None = None,
    ) -> bytes:
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
    """CI-safe visualizer stand-in: pure function, no network, deterministic.

    #227 2b: accepts the trailing negative (fixed protocol shape) and
    ignores it — the placeholder PNG stays a pure function of
    (prompt, seed, size), so existing determinism pins are untouched.

    020 P3 stage 2: likewise accepts (and ignores) the keyword-only
    reference_b64 so the stub remains a FluxTransport — no reference path
    exists for the stub renderer.
    """

    def generate(
        self, prompt: str, seed: int, size: str, negative: str = "",
        *, reference_b64: str | None = None,
    ) -> bytes:
        return _render_placeholder_png(prompt, seed, size)


def _failure_class(exc: BaseException) -> str:
    """#119 wave 3: bounded failure classification for the retry decision
    and the lifecycle logs.

    Only connection-refused is retryable (a refused connect never reached
    the GPU server, so one immediate retry cannot duplicate render work).
    The returned tag is a fixed vocabulary string — it never carries prompt,
    payload, or credential material.
    """
    import urllib.error

    # HTTP errors first: HTTPError is a URLError subclass and must never
    # retry regardless of what its .reason holds.
    if isinstance(exc, urllib.error.HTTPError):
        return "http_error"
    # URLError wraps the low-level cause in .reason; bare OSErrors arrive
    # unwrapped. String-valued reasons classify as their own (non-retryable)
    # class instead of blowing up the isinstance chain.
    reason = getattr(exc, "reason", None)
    target = exc if reason is None else reason
    if isinstance(target, str):
        return "url_error_string_reason"
    if isinstance(target, ConnectionRefusedError) or (
            isinstance(target, OSError) and target.errno == errno.ECONNREFUSED):
        return "connect_refused"
    if isinstance(target, ConnectionResetError) or (
            isinstance(target, OSError) and target.errno == errno.ECONNRESET):
        return "connection_reset"
    if isinstance(target, TimeoutError) or (
            isinstance(target, OSError) and target.errno == errno.ETIMEDOUT):
        return "timeout"
    if isinstance(target, socket.gaierror):
        return "dns_error"
    if isinstance(target, OSError):
        return "os_error"
    return "other"


class HttpFluxTransport:
    """Client of the external Flux+LoRA server (§15). Never makes game decisions."""

    def __init__(self, config: VisualConfig):
        self._config = config

    def generate(
        self, prompt: str, seed: int, size: str, negative: str = "",
        *, reference_b64: str | None = None,
    ) -> bytes:
        import urllib.error
        import urllib.request

        # #227 2b: fixed schema — "negative" is ALWAYS included (empty when
        # not composed); adapters that predate the field ignore it.
        # 020 P3 stage 2: "reference_b64" rides ONLY when set — None/empty
        # keeps the payload byte-identical to the legacy 6-key body (the
        # key is absent, never empty).
        payload: dict = {
            "prompt": prompt,
            "seed": seed,
            "size": size,
            "model": self._config.model,
            "lora": self._config.lora,
            "negative": negative,
        }
        if reference_b64:
            payload["reference_b64"] = reference_b64
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        # #119 wave 3: exactly ONE immediate retry of the SAME serialized
        # request (same bytes, same timeout), ONLY for connect-refused
        # failures — a refused connect never reached the server, so the
        # retry cannot duplicate GPU work. Resets (post-transmission
        # duplication risk), timeouts (would double the worst-case wait),
        # HTTP/DNS/other errors and response-read failures stay single-shot.
        # The retry lives entirely inside this one logical generate() call:
        # single-flight and negative-cache semantics upstream are untouched
        # (only the final failure propagates to the negative cache).
        for attempt in (1, 2):
            try:
                response = urllib.request.urlopen(
                    request, timeout=self._config.timeout_sec)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                failure = _failure_class(exc)
                if failure == "connect_refused":
                    if attempt == 1:
                        _LOGGER.warning(
                            "flux connect refused (attempt %d/2, class=%s); "
                            "retrying once", attempt, failure)
                        continue
                    _LOGGER.error(
                        "flux unavailable: connect refused on both attempts "
                        "(attempts=%d, class=%s)", attempt, failure)
                else:
                    _LOGGER.error(
                        "flux unavailable (attempts=%d, class=%s)",
                        attempt, failure)
                raise ValueError(f"flux unavailable: {exc}") from exc
            # Response-body reading stays OUTSIDE the retryable open branch:
            # a read failure means the request was already served — never
            # retried, still converted for the route's 502 mapping.
            try:
                with response:
                    body = response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _LOGGER.error(
                    "flux response read failed (attempts=%d, class=%s)",
                    attempt, _failure_class(exc))
                raise ValueError(f"flux unavailable: {exc}") from exc
            # "recovered" fires only after the body read SUCCEEDED (about to
            # return) — a read failure must not log a recovery it never had.
            if attempt == 2:
                _LOGGER.info("flux retry recovered (attempt 2/2)")
            return body


def build_transport(config: VisualConfig) -> FluxTransport:
    if config.transport == "stub":
        return StubFluxTransport()
    if config.transport == "http":
        return HttpFluxTransport(config)
    raise ValueError(f"unknown visual transport: {config.transport}")
