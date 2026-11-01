"""#119 wave 3 — HttpFluxTransport refusal-only single retry.

Contract under test (backend/app/visual/transports.py, HttpFluxTransport.generate):

- connect-refused failure (URLError wrapping ConnectionRefusedError, or
  OSError with errno ECONNREFUSED) -> exactly ONE immediate retry of the
  SAME serialized request (same bytes, same timeout); success on the retry
  returns the asset, exhaustion raises ValueError("flux unavailable: ...")
  with the causal exception chained (__cause__).
- NEVER retried: TimeoutError/ETIMEDOUT (would double the worst-case wait),
  ConnectionResetError/ECONNRESET (reset after transmission could duplicate
  GPU work), HTTP errors, DNS errors, string-valued URLError reasons, any
  other OSError — and failures while READING the response body (the request
  was already served; reading stays outside the retryable open branch).
- the legacy six-key payload pin is unchanged (no reference_b64 -> key
  absent, byte-identical body).
- lifecycle logs go to logger "vl1.visual" and carry attempt counts and
  error classification ONLY — never prompt, reference bytes, or request
  body content.

State boundaries: the retry lives entirely inside one logical generate()
call — single-flight and negative-cache semantics in visual_routes.py are
untouched (only the final failure reaches the negative cache).

Patching pattern follows tests/m234/test_reference_b64_transport.py:45-53
(stub urllib.request.urlopen; no network).
"""
import errno
import io
import json
import logging
import os
import socket
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import VisualConfig  # noqa: E402
from app.visual.transports import HttpFluxTransport  # noqa: E402

# Distinctive marker content: the log-hygiene tests assert these strings
# never appear in the captured lifecycle logs.
SECRET_PROMPT = "SECRET-SCENE-PROMPT-CONTENT"
SECRET_REFERENCE = "SECRET-REFERENCE-B64-CONTENT"


def _config():
    return VisualConfig(
        transport="http", base_url="http://flux.internal:7860",
        model="flux.1-schnell", lora="vl1-style", timeout_sec=5.0,
    )


def _refused():
    return urllib.error.URLError(
        ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"))


class _BrokenReadResponse:
    """Context-manager response whose body read fails post-connection."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        raise ConnectionResetError(errno.ECONNRESET, "reset during read")


def _install(monkeypatch, outcomes):
    """Patch urllib.request.urlopen (m234 pattern): each call pops the next
    scripted outcome — an Exception instance (raised) or a response object
    (returned). Records every call's request bytes and timeout."""
    calls = []
    scripted = list(outcomes)

    def fake_urlopen(request, timeout):
        calls.append({"data": request.data, "timeout": timeout})
        outcome = scripted.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


class TestRefusalRetry:
    def test_refusal_then_success_exactly_two_calls(self, monkeypatch):
        calls = _install(
            monkeypatch, [_refused(), io.BytesIO(b"\x89PNG recovered")])
        data = HttpFluxTransport(_config()).generate(
            SECRET_PROMPT, 7, "256x256")
        assert data == b"\x89PNG recovered"
        assert len(calls) == 2, "exactly one retry after a refused connect"

    def test_refusal_then_read_failure_no_recovered_log(
            self, monkeypatch, caplog):
        """Attempt 1 refused, attempt 2 connects but the body read fails:
        exactly two urlopen calls, ValueError with the read error as
        __cause__, and the recovered log must NOT fire for a call that
        ultimately failed (recovery is logged only after a successful
        read)."""
        calls = _install(monkeypatch, [_refused(), _BrokenReadResponse()])
        with caplog.at_level(logging.DEBUG, logger="vl1.visual"):
            with pytest.raises(ValueError) as excinfo:
                HttpFluxTransport(_config()).generate("p", 1, "8x8")
        assert str(excinfo.value).startswith("flux unavailable:")
        cause = excinfo.value.__cause__
        assert isinstance(cause, ConnectionResetError), cause
        assert cause.errno == errno.ECONNRESET
        assert len(calls) == 2
        assert "recovered" not in caplog.text

    def test_bare_connection_refused_error_retries_once(self, monkeypatch):
        """OSError errno ECONNREFUSED without the URLError wrapper retries."""
        calls = _install(
            monkeypatch,
            [ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
             io.BytesIO(b"ok")])
        assert HttpFluxTransport(_config()).generate("p", 1, "8x8") == b"ok"
        assert len(calls) == 2

    def test_urLError_wrapped_plain_oserror_econnrefused_retries(self, monkeypatch):
        calls = _install(
            monkeypatch,
            [urllib.error.URLError(OSError(errno.ECONNREFUSED, "refused")),
             io.BytesIO(b"ok")])
        assert HttpFluxTransport(_config()).generate("p", 1, "8x8") == b"ok"
        assert len(calls) == 2

    def test_refusal_exhaustion_value_error_with_cause(self, monkeypatch):
        first, second = _refused(), _refused()
        calls = _install(monkeypatch, [first, second])
        with pytest.raises(ValueError) as excinfo:
            HttpFluxTransport(_config()).generate("p", 1, "8x8")
        assert str(excinfo.value).startswith("flux unavailable:")
        assert excinfo.value.__cause__ is second
        assert len(calls) == 2, "exactly one retry, then the failure surfaces"

    def test_same_request_bytes_and_timeout_across_attempts(self, monkeypatch):
        calls = _install(monkeypatch, [_refused(), io.BytesIO(b"ok")])
        HttpFluxTransport(_config()).generate("p", 7, "256x256")
        assert len(calls) == 2
        assert calls[0]["data"] == calls[1]["data"], (
            "the retry must send the SAME serialized request bytes")
        assert calls[0]["timeout"] == calls[1]["timeout"] == 5.0


class TestNoRetryOnOtherFailures:
    @pytest.mark.parametrize(
        "outcome",
        [
            TimeoutError(errno.ETIMEDOUT, "timed out"),
            ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"),
            urllib.error.URLError(
                ConnectionResetError(errno.ECONNRESET, "reset")),
            urllib.error.HTTPError(
                "http://flux.internal:7860/generate", 503, "unavailable",
                None, None),
            urllib.error.URLError(
                socket.gaierror(-2, "Name or service not known")),
            urllib.error.URLError("getaddrinfo failed"),
            OSError(errno.EHOSTUNREACH, "No route to host"),
        ],
        ids=[
            "timeout", "connection_reset", "wrapped_connection_reset",
            "http_503", "dns_error", "string_reason", "other_oserror",
        ],
    )
    def test_single_call_then_value_error(self, monkeypatch, outcome):
        calls = _install(monkeypatch, [outcome])
        with pytest.raises(ValueError) as excinfo:
            HttpFluxTransport(_config()).generate("p", 1, "8x8")
        assert str(excinfo.value).startswith("flux unavailable:")
        assert excinfo.value.__cause__ is outcome
        assert len(calls) == 1, (
            f"failure class must NOT retry; calls={len(calls)}")

    def test_response_read_failure_never_retries(self, monkeypatch):
        """A body-read failure means the request was already served — no
        retry, exactly one urlopen call, still converted to ValueError."""
        calls = _install(monkeypatch, [_BrokenReadResponse()])
        with pytest.raises(ValueError) as excinfo:
            HttpFluxTransport(_config()).generate("p", 1, "8x8")
        assert str(excinfo.value).startswith("flux unavailable:")
        assert excinfo.value.__cause__ is not None
        assert len(calls) == 1


class TestPayloadPin:
    def test_legacy_six_key_payload_unchanged(self, monkeypatch):
        """Regression pin (m234 pattern): no reference_b64 -> the raw body is
        exactly the legacy six-key JSON, key absent (never empty)."""
        captured = {}

        def fake_urlopen(request, timeout):
            captured["raw"] = request.data
            return io.BytesIO(b"\x89PNG fake-bytes")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        HttpFluxTransport(_config()).generate("p", 1, "8x8")
        expected = json.dumps({
            "prompt": "p", "seed": 1, "size": "8x8",
            "model": "flux.1-schnell", "lora": "vl1-style", "negative": "",
        }).encode()
        assert captured["raw"] == expected, (
            f"legacy payload must stay byte-identical; got {captured['raw']!r}")
        data = json.loads(captured["raw"])
        assert sorted(data) == [
            "lora", "model", "negative", "prompt", "seed", "size"]
        assert "reference_b64" not in data


class TestLogHygiene:
    def test_retry_and_recovered_logs_carry_no_prompt_or_payload(
            self, monkeypatch, caplog):
        calls = _install(
            monkeypatch, [_refused(), io.BytesIO(b"\x89PNG ok")])
        with caplog.at_level(logging.DEBUG, logger="vl1.visual"):
            HttpFluxTransport(_config()).generate(
                SECRET_PROMPT, 7, "256x256", reference_b64=SECRET_REFERENCE)
        assert len(calls) == 2
        assert "retrying once" in caplog.text
        assert "recovered" in caplog.text
        # hygiene: no prompt, reference bytes, or request-body content
        assert SECRET_PROMPT not in caplog.text
        assert SECRET_REFERENCE not in caplog.text
        assert '"prompt"' not in caplog.text

    def test_exhausted_log_carries_attempt_count_and_class_only(
            self, monkeypatch, caplog):
        _install(monkeypatch, [_refused(), _refused()])
        with caplog.at_level(logging.DEBUG, logger="vl1.visual"):
            with pytest.raises(ValueError):
                HttpFluxTransport(_config()).generate(
                    SECRET_PROMPT, 1, "8x8")
        assert "attempts=2" in caplog.text
        assert "connect_refused" in caplog.text
        assert SECRET_PROMPT not in caplog.text

    def test_non_retryable_failure_logs_class_no_retry_event(
            self, monkeypatch, caplog):
        _install(monkeypatch, [TimeoutError(errno.ETIMEDOUT, "timed out")])
        with caplog.at_level(logging.DEBUG, logger="vl1.visual"):
            with pytest.raises(ValueError):
                HttpFluxTransport(_config()).generate(SECRET_PROMPT, 1, "8x8")
        assert "attempts=1" in caplog.text
        assert "class=timeout" in caplog.text
        assert "retrying" not in caplog.text
        assert SECRET_PROMPT not in caplog.text
