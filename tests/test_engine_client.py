"""EngineClient transport behavior: headers, error mapping, 422 passthrough."""

from __future__ import annotations

import json

import httpx
import pytest

from backtest360_mcp.engine_client import EngineClient, EngineError


def test_headers_sent(engine, mock_engine):
    engine.version()
    req = mock_engine.requests[-1]
    assert req.headers["x-api-key"] == "b360_testkey"
    assert req.headers["x-client-contract"] == "1"
    assert req.headers["x-client-version"].startswith("backtest360-mcp/")


def test_version_roundtrip(engine):
    info = engine.version()
    assert info["api_contract"] == "4"


def test_query_params(engine, mock_engine):
    engine.ticker_search("bitcoin", "crypto", 5)
    req = mock_engine.requests[-1]
    assert req.url.params["q"] == "bitcoin"
    assert req.url.params["asset_class"] == "crypto"
    assert req.url.params["limit"] == "5"


def test_strategies_forwards_all_filters(engine, mock_engine):
    engine.strategies(
        collection="all", q="cross", tags="trend,momentum",
        detail="compact", limit=25, offset=50,
    )
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/strategies"
    assert req.url.params["collection"] == "all"
    assert req.url.params["q"] == "cross"
    assert req.url.params["tags"] == "trend,momentum"
    assert req.url.params["detail"] == "compact"
    assert req.url.params["limit"] == "25"
    assert req.url.params["offset"] == "50"


def test_strategies_omits_absent_filters(engine, mock_engine):
    engine.strategies()
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/strategies"
    assert dict(req.url.params) == {}


def test_error_carries_code_and_request_id(engine, mock_engine):
    mock_engine.overrides["/api/backtest"] = (
        504,
        {"detail": {"code": "COMPUTE_TIMEOUT", "message": "Run exceeded limit"}},
        {"X-Request-ID": "req-123"},
    )
    with pytest.raises(EngineError) as exc_info:
        engine.backtest({"data_source": {}})
    err = exc_info.value
    assert err.status == 504
    assert err.code == "COMPUTE_TIMEOUT"
    assert err.request_id == "req-123"
    assert "exceeded" in str(err)


def test_error_retry_after_parsed(engine, mock_engine):
    mock_engine.overrides["/api/backtest"] = (
        429,
        {"detail": {"code": "QUOTA_EXCEEDED", "message": "Quota exhausted"}},
        {"Retry-After": "30"},
    )
    with pytest.raises(EngineError) as exc_info:
        engine.backtest({"data_source": {}})
    assert exc_info.value.retry_after == 30.0


def test_fastapi_list_detail_flattened(engine, mock_engine):
    mock_engine.overrides["/api/backtest"] = (
        422,
        {"detail": [
            {"loc": ["body", "data_source"], "msg": "field required"},
            {"loc": ["body", "execution", "fee_pct"], "msg": "must be >= 0"},
        ]},
        {},
    )
    with pytest.raises(EngineError) as exc_info:
        engine.backtest({})
    msg = str(exc_info.value)
    assert "data_source: field required" in msg
    assert "fee_pct" in msg


def test_export_returns_raw_bytes(engine, mock_engine):
    raw = engine.export({"run": {}, "legs": [{"id": "a", "data_source": {}}]})
    assert raw == b"PK\x03\x04fake-xlsx"
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/backtest/export"


def test_export_error_still_raises(engine, mock_engine):
    mock_engine.overrides["/api/backtest/export"] = (
        403, {"detail": {"code": "SCOPE_MISSING", "message": "needs metrics.full"}}, {},
    )
    with pytest.raises(EngineError) as exc_info:
        engine.export({"run": {}, "legs": []})
    assert exc_info.value.status == 403


def test_validate_422_returned_as_result(engine):
    out = engine.validate_strategy({"strategy": {"name": "bad"}})
    assert out["valid"] is False
    assert out["errors"][0]["code"] == "UNKNOWN_COLUMN_REF"


def test_validate_other_errors_still_raise(engine, mock_engine):
    mock_engine.overrides["/api/validate-strategy"] = (
        401, {"detail": {"code": "AUTH_INVALID", "message": "bad key"}}, {},
    )
    with pytest.raises(EngineError) as exc_info:
        engine.validate_strategy({"strategy": {}})
    assert exc_info.value.status == 401


def test_nan_payload_rejected_client_side(engine, mock_engine):
    with pytest.raises(EngineError) as exc_info:
        engine.backtest({"data_source": {"ohlcv": {"close": [float("nan")]}}})
    assert exc_info.value.code == "MCP_INVALID_PAYLOAD"
    assert not mock_engine.requests  # nothing reached the wire


def test_non_json_success_raises(settings):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>oops</html>")
    )
    client = EngineClient(settings.engine_url, settings.api_key, transport=transport)
    with pytest.raises(EngineError) as exc_info:
        client.version()
    assert exc_info.value.code == "MCP_MALFORMED_RESPONSE"


def test_plain_text_error_body(settings):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(502, text="Bad Gateway")
    )
    client = EngineClient(settings.engine_url, settings.api_key, transport=transport)
    with pytest.raises(EngineError) as exc_info:
        client.version()
    assert exc_info.value.status == 502
    assert "Bad Gateway" in str(exc_info.value)


def test_validate_body_passthrough(engine, mock_engine):
    engine.validate_strategy(
        {"strategy": {"name": "ok"}, "injected_indicators": ["ml_score"]}
    )
    sent = json.loads(mock_engine.requests[-1].content)
    assert sent["injected_indicators"] == ["ml_score"]


def test_price_history_omits_end_when_absent(engine, mock_engine):
    engine.price_history("BTC-USD", "2020-01-01", "daily", None)
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/data/history"
    assert req.url.params["start"] == "2020-01-01"
    assert req.url.params["frequency"] == "daily"
    assert "end" not in req.url.params


def test_price_history_sends_end_when_given(engine, mock_engine):
    engine.price_history("BTC-USD", "2020-01-01", "daily", "2021-01-01")
    assert mock_engine.requests[-1].url.params["end"] == "2021-01-01"


def test_macro_observations_omits_empty_bounds(engine, mock_engine):
    engine.macro_observations("treasury_10y", None, None)
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/data/macro"
    assert req.url.params["series"] == "treasury_10y"
    assert "start" not in req.url.params
    assert "end" not in req.url.params


def test_macro_catalog_omits_category_when_absent(engine, mock_engine):
    engine.macro_catalog(None)
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/data/macro/series"
    assert "category" not in req.url.params
