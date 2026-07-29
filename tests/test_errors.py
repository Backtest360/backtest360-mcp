"""Error mapping: every engine failure becomes agent-actionable guidance."""

from __future__ import annotations

from backtest360_mcp.engine_client import EngineError
from backtest360_mcp.errors import to_tool_error


def _msg(**kwargs) -> str:
    defaults = {"status": 500, "code": None, "request_id": None, "retry_after": None}
    defaults.update(kwargs)
    message = defaults.pop("message", "boom")
    return str(to_tool_error(EngineError(message, **defaults)))


def test_429_surfaces_retry_after():
    msg = _msg(status=429, code="QUOTA_EXCEEDED", retry_after=30.0)
    assert "QUOTA_EXCEEDED" in msg
    assert "Retry after 30 seconds" in msg


def test_429_without_header_still_says_wait():
    msg = _msg(status=429, code="QUOTA_EXCEEDED")
    assert "Wait before retrying" in msg


def test_504_says_do_not_retry():
    msg = _msg(status=504, code="COMPUTE_TIMEOUT")
    assert "Do NOT retry" in msg
    assert "reduce" in msg.lower()


def test_503_says_transient():
    msg = _msg(status=503, code="ENGINE_BUSY", retry_after=5.0)
    assert "retry with backoff" in msg
    assert "Retry after 5 seconds" in msg


def test_500_says_do_not_retry_and_keeps_request_id():
    msg = _msg(status=500, code="INTERNAL_ERROR", request_id="req-99")
    assert "Do NOT retry" in msg
    assert "req-99" in msg


def test_501_says_not_implemented_do_not_retry():
    msg = _msg(status=501, code="NOT_IMPLEMENTED")
    assert "does not implement" in msg
    assert "Do not retry" in msg


def test_502_says_transient_retry():
    msg = _msg(status=502, code="BAD_GATEWAY")
    assert "retry with backoff" in msg


def test_403_names_permission_problem():
    msg = _msg(status=403, code="PERMISSION_DENIED", message="this plan lacks a required feature")
    assert "permission" in msg.lower() or "feature" in msg.lower()
    assert "this plan lacks a required feature" in msg


def test_409_says_upgrade():
    msg = _msg(status=409, code="CLIENT_CONTRACT_MISMATCH")
    assert "Upgrade backtest360-mcp" in msg


def test_413_says_reduce_payload():
    msg = _msg(status=413, code="REQUEST_TOO_LARGE")
    assert "Reduce the payload" in msg


def test_request_id_included():
    msg = _msg(status=500, request_id="req-42")
    assert "req-42" in msg


def test_engine_code_used_as_label():
    msg = _msg(status=429, code="CONCURRENCY_EXCEEDED")
    assert msg.startswith("[CONCURRENCY_EXCEEDED]")


def test_status_used_as_label_when_no_code():
    msg = _msg(status=502)
    assert msg.startswith("[HTTP_502]")
