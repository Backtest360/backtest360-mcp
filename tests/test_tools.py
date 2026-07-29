"""Tool-level behavior through a real MCPServer instance against the mock engine."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from conftest import make_backtest_result

from backtest360_mcp import __version__
from backtest360_mcp.server import create_server
from backtest360_mcp.settings import Settings
from backtest360_mcp.shaping import HEADLINE_METRICS
from backtest360_mcp.tools import register_all


def test_server_reports_package_version() -> None:
    """serverInfo.version must be our package version, not the mcp SDK's.

    create_initialization_options() on the underlying low-level server falls
    back to pkg_version("mcp") when no version is set. create_server() passes
    our own version through MCPServer's constructor so the initialize
    handshake reports backtest360-mcp's own version instead.
    """
    mcp = create_server(Settings(engine_url="https://engine.test", api_key="b360_testkey"))
    assert mcp.version == __version__

STRATEGY = {
    "name": "sma-cross",
    "indicators": [
        {"ref": "sma_fast", "name": "SMA", "kind": "technical", "params": {"period": 10}},
        {"ref": "sma_slow", "name": "SMA", "kind": "technical", "params": {"period": 50}},
    ],
    "condition_tree": {
        "long_entry": {"op": "leaf", "expr": "sma_fast > sma_slow"},
        "long_exit": {"op": "leaf", "expr": "sma_fast < sma_slow"},
        "short_entry": None,
        "short_exit": None,
    },
}

DATA_SOURCE = {
    "ohlcv": {
        "dates": ["2020-01-01T00:00:00", "2020-01-02T00:00:00"],
        "open": [1.0, 1.0], "high": [1.0, 1.1], "low": [0.9, 1.0],
        "close": [1.0, 1.05], "volume": [10, 12],
    }
}


@pytest.fixture
def server(engine, settings) -> MCPServer:
    mcp = MCPServer("backtest360-test")
    register_all(mcp, engine, settings)
    return mcp


async def call(server: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    """Invoke a tool and return its structured result.

    Tools with concrete return types yield structured content; tools typed
    ``Any`` (mixed list/dict returns) yield JSON text content only — parse it,
    exactly as a text-only MCP client would.
    """
    result = await server.call_tool(name, arguments)
    if result.structured_content is not None:
        return result.structured_content
    content = result.content
    if content and hasattr(content[0], "text"):
        parsed = [json.loads(block.text) for block in content]
        return parsed[0] if len(parsed) == 1 else parsed
    return content


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "engine_info", "get_me", "get_catalog", "list_indicators",
    "list_templates", "get_strategy_schema", "validate_strategy",
    "validate_indicator",
    "run_backtest", "get_latest_signal", "compare_backtests",
    "export_backtest", "compute_stats",
    "search_tickers", "list_tickers", "get_data_range",
    "get_ticker_info", "get_quote", "get_price_history",
    "list_macro_series", "get_macro_series",
    "list_data_samples", "get_data_sample", "get_ticker_lookup",
}


async def test_all_tools_registered(server):
    tools = {t.name for t in await server.list_tools()}
    assert tools == EXPECTED_TOOLS


async def test_every_tool_has_description(server):
    for tool in await server.list_tools():
        assert tool.description and len(tool.description) > 40, tool.name


# ---------------------------------------------------------------------------
# catalogs
# ---------------------------------------------------------------------------

async def test_engine_info(server):
    info = await call(server, "engine_info", {})
    assert info["api_contract"] == "4"
    assert info["health"] == "ok"


async def test_get_catalog(server):
    sections = await call(server, "get_catalog", {"catalog": "sections"})
    assert sections["metrics"][0]["id"] == "sharpe"


async def test_get_catalog_sampling_modes(server):
    out = await call(server, "get_catalog", {"catalog": "sampling-modes"})
    assert out["modes"][0]["id"] == "iid_bootstrap"


async def test_get_catalog_thins_oversized_payload(server, mock_engine):
    huge = {"metrics": [{"id": f"m{i}", "blob": "x" * 300} for i in range(1000)]}
    mock_engine.overrides["/api/sections"] = (200, huge, {})
    out = await call(server, "get_catalog", {"catalog": "sections"})
    assert out["truncated_by_mcp"] is True
    assert len(out["metrics"]) < 1000  # the dominant list was thinned to fit


async def test_list_indicators_compact(server):
    out = await call(server, "list_indicators", {})
    assert out["count"] == len(out["indicators"])
    assert {e["id"] for e in out["indicators"]} == {"rsi", "sma"}
    assert all("params_schema" not in e for e in out["indicators"])
    # Compact carries only the lean discovery field set — description is
    # dropped to keep the scan small (available via name=<id>).
    assert all(
        set(e) == {"id", "name", "category", "kind", "value_dtype"}
        for e in out["indicators"]
    )


async def test_list_indicators_returns_single_content_block(server):
    # The bug was a bare-list return fanned out into one block PER indicator,
    # overflowing the client. A consolidated object must be exactly one block.
    result = await server.call_tool("list_indicators", {})
    assert len(result.content) == 1


async def test_list_indicators_large_catalog_stays_complete(server, mock_engine):
    # A realistic-size catalog must come back complete (never silently thinned)
    # under the default output cap — compact discovery is not truncated.
    big = [
        {"id": f"ind{i}", "name": f"IND{i}", "description": "x" * 40,
         "category": "Trend", "kind": "technical", "value_dtype": "numeric"}
        for i in range(251)
    ]
    mock_engine.overrides["/api/indicators"] = (200, {"indicators": big}, {})
    out = await call(server, "list_indicators", {})
    assert out["count"] == 251
    assert len(out["indicators"]) == 251
    assert "truncated_by_mcp" not in out


async def test_list_indicators_by_name_returns_full_schema(server):
    entry = await call(server, "list_indicators", {"name": "RSI"})
    assert entry["id"] == "rsi"
    assert "params_schema" in entry
    assert "description" in entry  # full entry keeps description (dropped only from compact)


async def test_list_indicators_unknown_name_is_actionable(server):
    with pytest.raises(ToolError, match="No indicator named"):
        await call(server, "list_indicators", {"name": "nope"})


def _last_indicators_descriptions(mock_engine) -> str | None:
    for req in reversed(mock_engine.requests):
        if req.url.path == "/api/indicators":
            return req.url.params.get("descriptions")
    return None


async def test_list_indicators_compact_requests_no_descriptions(server, mock_engine):
    # Wire optimization: compact discovery tells the engine to omit descriptions.
    await call(server, "list_indicators", {})
    assert _last_indicators_descriptions(mock_engine) == "false"


async def test_list_indicators_by_name_requests_descriptions(server, mock_engine):
    # The single-entry path needs the description, so it must request it.
    await call(server, "list_indicators", {"name": "RSI"})
    assert _last_indicators_descriptions(mock_engine) == "true"


async def test_list_indicators_full_requests_descriptions(server, mock_engine):
    # compact=False returns full entries for everything — descriptions included.
    out = await call(server, "list_indicators", {"compact": False})
    assert _last_indicators_descriptions(mock_engine) == "true"
    assert all("description" in e for e in out["indicators"])


# ---------------------------------------------------------------------------
# account
# ---------------------------------------------------------------------------

async def test_get_me(server):
    out = await call(server, "get_me", {})
    assert out["scopes"] == ["backtest.run", "meta.read", "strategy.validate"]
    assert out["limits"]["rpm"] == 30
    assert out["usage"]["minute"]["remaining"] == 27
    assert out["capabilities"]["server_side_fetch"] is False


async def test_get_me_rejected_key_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/me"] = (
        401, {"detail": {"code": "AUTH_INVALID", "message": "bad key"}}, {},
    )
    with pytest.raises(ToolError, match="BACKTEST360_API_KEY"):
        await call(server, "get_me", {})


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

async def test_list_templates_compact(server):
    out = await call(server, "list_templates", {})
    assert out["count"] == 2
    assert [e["id"] for e in out["templates"]] == ["sma-cross", "rsi-reversion"]
    # Compact carries only the selection fields — the heavy definition blocks
    # (condition_tree, indicators, parameter metadata) come via name=<id>.
    assert all(
        set(e) == {"id", "origin", "name", "description"}
        for e in out["templates"]
    )


async def test_list_templates_returns_single_content_block(server):
    # Same consolidation contract as list_indicators: one object, one block.
    result = await server.call_tool("list_templates", {})
    assert len(result.content) == 1


async def test_list_templates_by_name_returns_full_definition(server):
    entry = await call(server, "list_templates", {"name": "SMA Cross"})
    assert entry["id"] == "sma-cross"
    assert entry["condition_tree"]["long_entry"]["expr"] == "sma_fast > sma_slow"
    assert [i["ref"] for i in entry["indicators"]] == ["sma_fast", "sma_slow"]
    assert entry["defaults"] == {"sma_fast.period": 10}
    assert entry["locked_params"] == []


async def test_list_templates_lookup_is_case_insensitive_by_id(server):
    entry = await call(server, "list_templates", {"name": "RSI-Reversion"})
    assert entry["id"] == "rsi-reversion"


async def test_list_templates_unknown_name_is_actionable(server):
    with pytest.raises(ToolError, match="No template named"):
        await call(server, "list_templates", {"name": "nope"})


async def test_list_templates_full(server):
    out = await call(server, "list_templates", {"compact": False})
    assert out["count"] == 2
    assert all("condition_tree" in e for e in out["templates"])


async def test_list_templates_caps_oversized_full_catalog(server, mock_engine):
    big = {
        "strategies": [
            {"id": f"t{i}", "origin": "system", "name": f"T{i}",
             "description": "x", "condition_tree": {"blob": "x" * 2_000},
             "indicators": [], "requires": {}, "defaults": {},
             "locked_params": []}
            for i in range(200)
        ],
        "total": 200,
    }
    mock_engine.overrides["/api/strategies"] = (200, big, {})
    out = await call(server, "list_templates", {"compact": False, "collection": "all"})
    req = mock_engine.requests[-1]
    assert req.url.params["collection"] == "all"
    assert out["truncated_by_mcp"] is True
    assert out["total"] == 200
    assert len(out["templates"]) < 200
    assert "name=<id>" in out["more"]


async def test_list_templates_default_call_matches_old_request_shape(server, mock_engine):
    # Byte-compatible with the pre-filter behavior: the default call still
    # requests only the curated set at the default page size, nothing else.
    await call(server, "list_templates", {})
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/strategies"
    assert req.url.params["collection"] == "curated"
    assert req.url.params["limit"] == "50"
    assert "q" not in req.url.params
    assert "tags" not in req.url.params


async def test_list_templates_default_stays_under_the_cap(server):
    out = await call(server, "list_templates", {})
    assert "truncated_by_mcp" not in out


async def test_list_templates_forwards_filters_verbatim(server, mock_engine):
    await call(
        server, "list_templates",
        {"collection": "all", "q": "cross", "tags": "trend,momentum", "limit": 25},
    )
    req = mock_engine.requests[-1]
    assert req.url.params["collection"] == "all"
    assert req.url.params["q"] == "cross"
    assert req.url.params["tags"] == "trend,momentum"
    assert req.url.params["limit"] == "25"


async def test_list_templates_name_lookup_searches_all_collections(server, mock_engine):
    await call(server, "list_templates", {"name": "SMA Cross"})
    req = mock_engine.requests[-1]
    assert req.url.path == "/api/strategies"
    assert req.url.params["collection"] == "all"


async def test_list_templates_surfaces_pagination_fields(server, mock_engine):
    resp = {
        "strategies": [
            {"id": "sma-cross", "origin": "system", "name": "SMA Cross",
             "description": "Fast/slow moving-average crossover."},
        ],
        "count": 1,
        "total": 87,
        "next_offset": 50,
    }
    mock_engine.overrides["/api/strategies"] = (200, resp, {})
    out = await call(server, "list_templates", {})
    assert out["count"] == 1
    assert out["total"] == 87
    assert out["next_offset"] == 50


# ---------------------------------------------------------------------------
# validate — the fix-and-retry loop
# ---------------------------------------------------------------------------

async def test_validate_ok(server):
    out = await call(server, "validate_strategy", {"strategy": STRATEGY})
    assert out["valid"] is True


async def test_validate_failure_is_a_result_not_an_error(server):
    out = await call(server, "validate_strategy", {"strategy": {"name": "bad"}})
    assert out["valid"] is False
    err = out["errors"][0]
    # The agent's repair loop needs code + location + context, verbatim.
    assert err["code"] == "UNKNOWN_COLUMN_REF"
    assert err["location"] == "/condition_tree/long_entry/"
    assert err["context"]["available"] == ["close", "rsi_14"]


async def test_validate_indicator_ok(server):
    out = await call(server, "validate_indicator", {"name": "rsi", "params": {"period": 14}})
    assert out["valid"] is True
    assert out["warmup_bars"] == 14


async def test_validate_indicator_failure_is_a_result_not_an_error(server):
    out = await call(server, "validate_indicator", {"name": "not-a-real-indicator"})
    assert out["valid"] is False
    err = out["errors"][0]
    # The agent's repair loop needs code + location + message, verbatim.
    assert err["code"] == "UNKNOWN_INDICATOR"
    assert err["location"] == "/name"
    assert "not-a-real-indicator" in err["message"]


async def test_validate_indicator_sends_optional_fields(server, mock_engine):
    await call(
        server, "validate_indicator",
        {"name": "sma", "params": {"period": 10}, "upstream": ["rsi_14"]},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/validate-indicator")
    body = json.loads(req.content)
    assert body == {"name": "sma", "params": {"period": 10}, "upstream": ["rsi_14"]}


async def test_validate_indicator_omits_empty_optional_fields(server, mock_engine):
    await call(server, "validate_indicator", {"name": "rsi"})
    req = next(r for r in mock_engine.requests if r.url.path == "/api/validate-indicator")
    body = json.loads(req.content)
    assert body == {"name": "rsi"}


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------

async def test_run_backtest_summary(server):
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY},
    )
    assert out["response_detail"] == "summary"
    assert set(out["stats"]) == set(HEADLINE_METRICS)
    assert "series" not in out


async def test_run_backtest_full(server):
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "response_detail": "full", "trades_limit": 10},
    )
    assert out["trades_returned"] == 10
    assert out["series"]["points_returned"] <= 501


async def test_run_backtest_max_series_points_default_unchanged(server):
    # No max_series_points given — today's fixed 500-point cap still applies.
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "response_detail": "full"},
    )
    assert out["series"]["points_returned"] <= 501
    assert out["series"]["downsampled_from_bars"] == 1000


async def test_run_backtest_max_series_points_custom_cap_honored(server):
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "response_detail": "full", "max_series_points": 50},
    )
    assert out["series"]["points_returned"] <= 51
    assert out["series"]["downsampled_from_bars"] == 1000


async def test_run_backtest_max_series_points_covers_all_bars_no_downsampling(
    server, mock_engine
):
    # Cap >= the run's bar count — same no-downsampling path as a small run:
    # full-resolution series, no downsampled_from_bars/points_returned markers.
    # A small bar count keeps the whole 'full' payload under the output-size
    # cap, so _enforce_cap's own re-thinning doesn't mask the assertion.
    mock_engine.backtest_result = make_backtest_result(n_bars=60, n_trades=3)
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "response_detail": "full", "max_series_points": 60},
    )
    assert len(out["series"]["dates"]) == 60
    assert "downsampled_from_bars" not in out["series"]
    assert "points_returned" not in out["series"]


async def test_run_backtest_invalid_max_series_points_is_fixable_without_running(
    server, mock_engine
):
    # Below the floor of 2 must be caught BEFORE the quota-counted backtest
    # runs, mirroring the invalid-include pre-flight check.
    out = await call(server, "run_backtest",
                     {"data_source": DATA_SOURCE, "strategy": STRATEGY,
                      "max_series_points": 1})
    assert out["accepted"] is False
    assert out["error"]["detail"]["code"] == "INVALID_MAX_SERIES_POINTS"
    assert not any(r.url.path == "/api/backtest" for r in mock_engine.requests)


async def test_run_backtest_engine_rejection_is_fixable_result(server, mock_engine):
    mock_engine.overrides["/api/backtest"] = (
        422,
        {"detail": {"code": "INVALID_FREQUENCY_COMBO",
                    "message": "hourly signal with daily execution"}},
        {},
    )
    out = await call(
        server, "run_backtest", {"data_source": DATA_SOURCE, "strategy": STRATEGY},
    )
    assert out["accepted"] is False
    assert out["error"]["detail"]["code"] == "INVALID_FREQUENCY_COMBO"
    assert "validate_strategy" in out["hint"]


async def test_run_backtest_quota_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/backtest"] = (
        429,
        {"detail": {"code": "QUOTA_EXCEEDED", "message": "quota"}},
        {"Retry-After": "60"},
    )
    with pytest.raises(ToolError, match="Retry after 60 seconds"):
        await call(server, "run_backtest",
                   {"data_source": DATA_SOURCE, "strategy": STRATEGY})


async def test_run_backtest_invalid_include_is_fixable_without_running(server, mock_engine):
    # An invalid include must be caught BEFORE the quota-counted backtest runs:
    # a fixable result naming the bad value and the valid set, and no engine call.
    out = await call(server, "run_backtest",
                     {"data_source": DATA_SOURCE, "strategy": STRATEGY,
                      "include": ["alpha_curve"]})
    assert out["accepted"] is False
    message = out["error"]["detail"]["message"]
    assert "alpha_curve" in message
    assert "equity_curve" in message  # the valid set is listed
    assert not any(r.url.path == "/api/backtest" for r in mock_engine.requests)


async def test_run_backtest_valid_include_still_runs(server, mock_engine):
    out = await call(server, "run_backtest",
                     {"data_source": DATA_SOURCE, "strategy": STRATEGY,
                      "include": ["trades"]})
    assert out["response_detail"] == "summary"
    assert out["trades_returned"] >= 1  # the requested block is present
    assert any(r.url.path == "/api/backtest" for r in mock_engine.requests)


async def test_run_backtest_include_signal_diagnostics(server, mock_engine):
    out = await call(server, "run_backtest",
                     {"data_source": DATA_SOURCE, "strategy": STRATEGY,
                      "include": ["signal_diagnostics"]})
    diag = out["signal_diagnostics"]
    assert diag["available"] is True
    assert "long_entry_fired" in diag["conditions"]
    assert "fires_returned" in diag["conditions"]["long_entry_fired"]
    assert "fires_total" in diag["conditions"]["long_entry_fired"]


async def test_run_backtest_without_include_omits_signal_diagnostics(server):
    out = await call(server, "run_backtest",
                     {"data_source": DATA_SOURCE, "strategy": STRATEGY,
                      "response_detail": "full"})
    assert "signal_diagnostics" not in out
    assert "signal_diagnostics" in out["omitted_blocks"]


async def test_run_backtest_forwards_off_anchors(server, mock_engine):
    # off_anchors (an OffAnchorReport) is a small diagnostic block, same
    # family as data_quality/markers — it must reach the caller, not be
    # silently dropped by shaping at any detail level.
    mock_engine.backtest_result["off_anchors"] = {
        "open_count": 2, "close_count": 1, "strict": False,
        "events": [{"bar_idx": 5, "anchor": "open", "target_hour": 9.5,
                    "timestamp": "2020-01-06T00:00:00", "chosen_idx": 0}],
    }
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY},
    )
    assert out["off_anchors"]["open_count"] == 2
    assert out["off_anchors"]["events"][0]["anchor"] == "open"


async def test_run_backtest_omits_off_anchors_when_absent(server):
    # The fixture's off_anchors is None by default — the field must not
    # appear at all (never a spurious null key).
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY},
    )
    assert "off_anchors" not in out


async def test_latest_signal(server):
    out = await call(
        server, "get_latest_signal",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY},
    )
    assert out["signal"] == 1
    assert out["long_entry_fired"] is True


async def test_run_backtest_with_benchmark_returns_relative_metrics(server):
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "benchmark": DATA_SOURCE, "response_detail": "stats"},
    )
    assert out["benchmark_relative"]["beta"] == 0.9
    assert out["alignment"]["shared_bars"]


async def test_run_backtest_without_benchmark_has_no_relative_metrics(server):
    out = await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY, "response_detail": "stats"},
    )
    assert "benchmark_relative" not in out
    assert "alignment" not in out


async def test_run_backtest_forwards_run_overrides(server, mock_engine):
    # run.start/run.end/run.signal_frequency are run-level overrides that
    # apply across every leg -- must reach the request body verbatim.
    await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "run_start": "2021-01-01", "run_end": "2021-12-31",
         "run_signal_frequency": "hourly"},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    assert body["run"]["start"] == "2021-01-01"
    assert body["run"]["end"] == "2021-12-31"
    assert body["run"]["signal_frequency"] == "hourly"


async def test_run_backtest_run_overrides_omitted_by_default(server, mock_engine):
    await call(server, "run_backtest", {"data_source": DATA_SOURCE, "strategy": STRATEGY})
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    assert "start" not in body["run"]
    assert "end" not in body["run"]
    assert "signal_frequency" not in body["run"]


async def test_run_backtest_forwards_include_leaf_series(server, mock_engine):
    await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY, "include_leaf_series": True},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "strategy")
    assert leg["include_leaf_series"] is True


async def test_run_backtest_omits_include_leaf_series_by_default(server, mock_engine):
    await call(server, "run_backtest", {"data_source": DATA_SOURCE, "strategy": STRATEGY})
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "strategy")
    assert "include_leaf_series" not in leg


async def test_run_backtest_forwards_benchmark_exposure(server, mock_engine):
    await call(
        server, "run_backtest",
        {"data_source": DATA_SOURCE, "strategy": STRATEGY,
         "benchmark": DATA_SOURCE, "benchmark_exposure": 2.5},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "benchmark")
    assert leg["exposure"] == 2.5


async def test_compare(server):
    out = await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY},
                        {"label": "B", "strategy": STRATEGY}]},
    )
    assert [s["label"] for s in out["strategies"]] == ["A", "B"]


async def test_compare_with_benchmark_returns_relative_metrics(server):
    out = await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "include_benchmark": True},
    )
    strat = next(s for s in out["strategies"] if s["label"] == "A")
    assert strat["relative"]["beta"] == 0.9


async def test_compare_preserves_arbitrary_labels(server, mock_engine):
    # Labels need not be id-safe — the engine sees slugified leg ids, but the
    # response is keyed back to the caller's original labels.
    out = await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "My Strategy! #1", "strategy": STRATEGY},
                        {"label": "My Strategy! #1", "strategy": STRATEGY}]},
    )
    assert [s["label"] for s in out["strategies"]] == ["My Strategy! #1"] * 2
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    ids = [leg["id"] for leg in body["legs"]]
    assert len(ids) == len(set(ids))  # de-duplicated


async def test_compare_forwards_data_inputs(server, mock_engine):
    # data_inputs on a strategies[] entry must reach the engine leg — it was
    # previously silently dropped by _build_legs_body.
    data_inputs = {"ml_score": {"dates": ["2020-01-01T00:00:00"], "values": [0.5]}}
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY,
                        "data_inputs": data_inputs}]},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "a")
    assert leg["data_inputs"] == data_inputs


async def test_compare_forwards_run_overrides(server, mock_engine):
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "run_start": "2021-01-01", "run_end": "2021-12-31",
         "run_signal_frequency": "hourly"},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    assert body["run"]["start"] == "2021-01-01"
    assert body["run"]["end"] == "2021-12-31"
    assert body["run"]["signal_frequency"] == "hourly"


async def test_compare_independent_benchmark_data_source(server, mock_engine):
    # `benchmark` lets the caller give the benchmark leg its own data source,
    # independent of the shared `data_source` every strategy leg uses.
    benchmark_source = {
        "symbol": "SPY", "start": "2020-01-01", "end": "2020-12-31",
        "frequency": "daily",
    }
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "benchmark": benchmark_source},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    bench_leg = next(leg for leg in body["legs"] if leg["id"] == "benchmark")
    assert bench_leg["data_source"] == benchmark_source
    assert bench_leg["benchmark"] is True
    assert body["run"]["reference"] == "benchmark"


async def test_compare_benchmark_param_alone_adds_leg(server, mock_engine):
    # Passing `benchmark` alone (include_benchmark left False) must still add
    # the leg -- mirrors run_backtest's `benchmark` param semantics.
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "benchmark": DATA_SOURCE},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    assert any(leg["id"] == "benchmark" for leg in body["legs"])


async def test_compare_reference_label_names_a_strategy_leg(server, mock_engine):
    # run.reference must be settable to ANY leg id, not just the auto-added
    # benchmark leg -- resolved here by the caller's own label.
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY},
                        {"label": "B", "strategy": STRATEGY}],
         "reference_label": "A"},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    assert body["run"]["reference"] == "a"


async def test_compare_reference_label_unknown_is_tool_error(server, mock_engine):
    with pytest.raises(ToolError, match="reference_label"):
        await call(
            server, "compare_backtests",
            {"data_source": DATA_SOURCE,
             "strategies": [{"label": "A", "strategy": STRATEGY}],
             "reference_label": "nope"},
        )


async def test_compare_reference_label_ambiguous_duplicate_is_tool_error(server, mock_engine):
    with pytest.raises(ToolError, match="more than one"):
        await call(
            server, "compare_backtests",
            {"data_source": DATA_SOURCE,
             "strategies": [{"label": "A", "strategy": STRATEGY},
                            {"label": "A", "strategy": STRATEGY}],
             "reference_label": "A"},
        )


async def test_compare_allows_signals_leg(server, mock_engine):
    # The engine allows mixing strategy/signals/benchmark legs in a
    # comparison -- a strategies[] entry may supply signals instead of a
    # strategy document.
    signals = {
        "dates": ["2020-01-01T00:00:00", "2020-01-02T00:00:00"],
        "values": [0, 1],
    }
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "external", "signals": signals}]},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "external")
    assert leg["signals"] == signals
    assert "strategy" not in leg


async def test_compare_missing_strategy_and_signals_is_tool_error(server, mock_engine):
    with pytest.raises(ToolError, match="strategy.*signals|signals.*strategy"):
        await call(
            server, "compare_backtests",
            {"data_source": DATA_SOURCE, "strategies": [{"label": "A"}]},
        )


async def test_compare_forwards_per_leg_exposure_and_include_leaf_series(server, mock_engine):
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY,
                        "exposure": 2.0, "include_leaf_series": True}]},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "a")
    assert leg["exposure"] == 2.0
    assert leg["include_leaf_series"] is True


async def test_compare_forwards_benchmark_exposure(server, mock_engine):
    await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "include_benchmark": True, "benchmark_exposure": 3.0},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest")
    body = json.loads(req.content)
    bench_leg = next(leg for leg in body["legs"] if leg["id"] == "benchmark")
    assert bench_leg["exposure"] == 3.0


async def test_compare_hint_does_not_advertise_include(server):
    # compare_backtests has no ``include`` parameter, so its per-strategy
    # deeper-detail hint must not tell the agent to pass one.
    out = await call(
        server, "compare_backtests",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}]},
    )
    hint = out["strategies"][0]["result"]["more"]
    assert "include" not in hint
    assert "response_detail" in hint


async def test_export_backtest_returns_base64_workbook(server):
    out = await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}]},
    )
    assert out["content_type"].endswith("spreadsheetml.sheet")
    assert out["size_bytes"] > 0
    assert base64.b64decode(out["content_base64"]) == b"PK\x03\x04fake-xlsx"


async def test_export_backtest_oversized_is_tool_error(engine):
    # A tiny output cap forces the base64-encoded workbook over the limit.
    tiny_settings = Settings(
        engine_url="https://engine.test", api_key="b360_testkey", max_output_bytes=4,
    )
    mcp = MCPServer("backtest360-test-tiny")
    register_all(mcp, engine, tiny_settings)
    with pytest.raises(ToolError, match="too large"):
        await call(
            mcp, "export_backtest",
            {"data_source": DATA_SOURCE,
             "strategies": [{"label": "A", "strategy": STRATEGY}]},
        )


async def test_export_backtest_forwards_data_inputs(server, mock_engine):
    data_inputs = {"ml_score": {"dates": ["2020-01-01T00:00:00"], "values": [0.5]}}
    await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY,
                        "data_inputs": data_inputs}]},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest/export")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "a")
    assert leg["data_inputs"] == data_inputs


async def test_export_backtest_forwards_run_overrides(server, mock_engine):
    await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}],
         "run_start": "2021-01-01"},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest/export")
    body = json.loads(req.content)
    assert body["run"]["start"] == "2021-01-01"


async def test_export_backtest_reference_label_and_independent_benchmark(server, mock_engine):
    benchmark_source = {
        "symbol": "SPY", "start": "2020-01-01", "end": "2020-12-31",
        "frequency": "daily",
    }
    await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY},
                        {"label": "B", "strategy": STRATEGY}],
         "reference_label": "A", "benchmark": benchmark_source},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest/export")
    body = json.loads(req.content)
    # explicit reference_label wins over the auto-added benchmark leg
    assert body["run"]["reference"] == "a"
    bench_leg = next(leg for leg in body["legs"] if leg["id"] == "benchmark")
    assert bench_leg["data_source"] == benchmark_source


async def test_export_backtest_forwards_per_leg_and_benchmark_exposure(server, mock_engine):
    await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY, "exposure": 2.0,
                        "include_leaf_series": True}],
         "include_benchmark": True, "benchmark_exposure": 4.0},
    )
    req = next(r for r in mock_engine.requests if r.url.path == "/api/backtest/export")
    body = json.loads(req.content)
    leg = next(leg for leg in body["legs"] if leg["id"] == "a")
    assert leg["exposure"] == 2.0
    assert leg["include_leaf_series"] is True
    bench_leg = next(leg for leg in body["legs"] if leg["id"] == "benchmark")
    assert bench_leg["exposure"] == 4.0


async def test_export_backtest_engine_rejection_is_fixable_result(server, mock_engine):
    mock_engine.overrides["/api/backtest/export"] = (
        422,
        {"detail": {"code": "INVALID_LEG_COUNT", "message": "at least 1 leg required"}},
        {},
    )
    out = await call(
        server, "export_backtest",
        {"data_source": DATA_SOURCE,
         "strategies": [{"label": "A", "strategy": STRATEGY}]},
    )
    assert out["accepted"] is False
    assert out["error"]["detail"]["code"] == "INVALID_LEG_COUNT"


# ---------------------------------------------------------------------------
# analysis + data
# ---------------------------------------------------------------------------

async def test_compute_stats(server):
    out = await call(
        server, "compute_stats",
        {"returns": {"dates": ["2020-01-01T00:00:00"], "values": [0.01]},
         "trading_days_per_year": 252},
    )
    assert out["stats"]["sharpe"] == 1.2


async def test_compute_stats_forwards_annualization_factor(server, mock_engine):
    # trading_days_per_year is required and must reach the engine verbatim —
    # 365 for a crypto series, not a silent 252 default.
    await call(
        server, "compute_stats",
        {"returns": {"dates": ["2020-01-01T00:00:00"], "values": [0.01]},
         "trading_days_per_year": 365},
    )
    stats_req = next(r for r in mock_engine.requests if r.url.path == "/api/stats")
    body = json.loads(stats_req.content)
    assert body["config"]["trading_days_per_year"] == 365


async def test_search_tickers(server):
    out = await call(server, "search_tickers", {"query": "bitcoin"})
    assert out["results"][0]["ticker"] == "BTC-USD"


async def test_search_tickers_caps_large_result(server, mock_engine):
    big = [{"ticker": f"T{i}", "asset_class": "stocks"} for i in range(500)]
    mock_engine.overrides["/api/data/search"] = (
        200, {"status": "success", "count": len(big), "results": big}, {},
    )
    out = await call(server, "search_tickers", {"query": "t", "limit": 500})
    assert len(out["results"]) == 200
    assert out["truncated_by_mcp"] is True


async def test_list_tickers_small_result_unchanged(server):
    out = await call(server, "list_tickers", {})
    assert out["results"][0]["ticker"] == "BTC-USD"
    assert "truncated_by_mcp" not in out  # under the cap → no marker


async def test_list_tickers_caps_large_universe(server, mock_engine):
    big = [{"ticker": f"T{i}", "asset_class": "stocks"} for i in range(500)]
    mock_engine.overrides["/api/data/tickers"] = (
        200, {"status": "success", "count": len(big), "results": big}, {},
    )
    out = await call(server, "list_tickers", {})
    assert len(out["results"]) == 200
    assert out["returned"] == 200
    assert out["total"] == 500
    assert out["truncated_by_mcp"] is True
    assert "search_tickers" in out["more"]


async def test_get_data_range(server):
    out = await call(server, "get_data_range",
                     {"symbol": "BTC-USD", "frequency": "daily"})
    assert out["estimated_bars"] == 4285


# ---------------------------------------------------------------------------
# research data tools
# ---------------------------------------------------------------------------

async def test_get_ticker_info(server):
    out = await call(server, "get_ticker_info", {"symbol": "BTC-USD"})
    assert out["info"]["asset_class"] == "crypto"
    assert out["coverage"]["estimated_bars"] == 4285


async def test_get_ticker_info_not_found_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/data/info"] = (
        404, {"detail": {"code": "NOT_FOUND", "message": "Symbol not found: ZZZ"}}, {},
    )
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await call(server, "get_ticker_info", {"symbol": "ZZZ"})


async def test_get_quote(server):
    out = await call(server, "get_quote", {"symbol": "BTC-USD"})
    assert out["price"] == 68000.0
    assert out["as_of"] == "2026-06-11T00:00:00+00:00"


async def test_get_quote_forwards_frequency(server, mock_engine):
    await call(server, "get_quote", {"symbol": "BTC-USD", "frequency": "hourly"})
    req = next(r for r in mock_engine.requests if r.url.path == "/api/data/quote")
    assert req.url.params["frequency"] == "hourly"


async def test_get_price_history_downsamples_long_history(server):
    out = await call(server, "get_price_history",
                     {"symbol": "BTC-USD", "start": "2020-01-01"})
    ohlcv = out["ohlcv"]
    # 1000 bars thinned to the bounded point count, endpoints preserved.
    assert ohlcv["downsampled_from_bars"] == 1000
    assert ohlcv["points_returned"] <= 500
    assert len(ohlcv["dates"]) == ohlcv["points_returned"]
    assert len(ohlcv["close"]) == ohlcv["points_returned"]
    # The true bar count is preserved in the untouched summary.
    assert out["summary"]["total_bars"] == 1000


async def test_get_price_history_short_history_unchanged(server, mock_engine):
    dates = ["2023-01-01T00:00:00", "2023-01-02T00:00:00", "2023-01-03T00:00:00"]
    mock_engine.overrides["/api/data/history"] = (200, {
        "status": "success",
        "summary": {"symbol": "AAA", "total_bars": 3, "frequency": "daily"},
        "market_hours": {},
        "ohlcv": {"dates": dates, "open": [1.0, 1.1, 1.2], "high": [1.0, 1.1, 1.2],
                  "low": [1.0, 1.1, 1.2], "close": [1.0, 1.1, 1.2],
                  "volume": [1, 2, 3]},
    }, {})
    out = await call(server, "get_price_history",
                     {"symbol": "AAA", "start": "2023-01-01"})
    # Under the point cap → no downsampling, no markers.
    assert out["ohlcv"]["dates"] == dates
    assert "downsampled_from_bars" not in out["ohlcv"]
    assert "truncated_by_mcp" not in out


async def test_get_price_history_requires_start(server):
    # start is a required parameter — omitting it is a tool-argument error.
    with pytest.raises(Exception):
        await call(server, "get_price_history", {"symbol": "BTC-USD"})


async def test_get_price_history_bar_limit_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/data/history"] = (
        413,
        {"detail": {"code": "BAR_LIMIT_EXCEEDED",
                    "message": "Requested window exceeds this key's per-run cap."}},
        {},
    )
    with pytest.raises(ToolError, match="BAR_LIMIT_EXCEEDED"):
        await call(server, "get_price_history",
                   {"symbol": "BTC-USD", "start": "1990-01-01"})


async def test_list_macro_series_catalog(server):
    out = await call(server, "list_macro_series", {})
    assert {s["id"] for s in out["series"]} == {"treasury_10y", "cpi"}
    assert "rates" in out["categories"]


async def test_list_macro_series_forwards_category(server, mock_engine):
    await call(server, "list_macro_series", {"category": "rates"})
    req = next(r for r in mock_engine.requests
               if r.url.path == "/api/data/macro/series")
    assert req.url.params["category"] == "rates"


async def test_get_macro_series_observations_downsampled(server):
    out = await call(server, "get_macro_series", {"series": "treasury_10y"})
    obs = out["observations"]
    assert obs["downsampled_from_bars"] == 800
    assert obs["points_returned"] <= 500
    assert len(obs["dates"]) == len(obs["values"]) == obs["points_returned"]
    # The series descriptor and as_of pass through untouched.
    assert out["series"]["id"] == "treasury_10y"


async def test_get_macro_series_unknown_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/data/macro"] = (
        404, {"detail": {"code": "NOT_FOUND", "message": "Unknown macro series 'nope'."}}, {},
    )
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await call(server, "get_macro_series", {"series": "nope"})


async def test_list_data_samples(server):
    out = await call(server, "list_data_samples", {})
    assert out["symbols"] == ["SPY", "QQQ", "BTC"]


async def test_get_data_sample(server):
    out = await call(server, "get_data_sample", {"symbol": "SPY"})
    assert out["summary"]["source"] == "sample"
    assert out["summary"]["total_bars"] == 250
    assert len(out["ohlcv"]["dates"]) == 250


async def test_get_data_sample_unknown_symbol_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/data/sample"] = (
        400, {"detail": {"code": "INVALID_SYMBOL", "message": "Unknown sample symbol: ZZZ"}}, {},
    )
    with pytest.raises(ToolError, match="INVALID_SYMBOL"):
        await call(server, "get_data_sample", {"symbol": "ZZZ"})


async def test_get_ticker_lookup(server):
    out = await call(server, "get_ticker_lookup", {"ticker": "AAPL"})
    assert out["result"]["ticker"] == "AAPL"
    assert out["result"]["asset_class"] == "stocks"


async def test_get_ticker_lookup_not_found_is_tool_error(server, mock_engine):
    mock_engine.overrides["/api/data/lookup/ZZZ"] = (
        404, {"detail": {"code": "NOT_FOUND", "message": "Unknown ticker: ZZZ"}}, {},
    )
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await call(server, "get_ticker_lookup", {"ticker": "ZZZ"})


# ---------------------------------------------------------------------------
# resources
# ---------------------------------------------------------------------------

async def test_catalog_resource(server):
    blocks = await server.read_resource("backtest360://catalog/sections")
    text = list(blocks)[0].content
    assert "sharpe" in text


async def test_catalog_resource_engine_error_is_graceful(server, mock_engine):
    # An engine failure inside a resource must not raise out of the read —
    # it returns a readable JSON error instead.
    mock_engine.overrides["/api/sections"] = (
        503, {"detail": {"code": "AT_CAPACITY", "message": "busy"}}, {},
    )
    blocks = await server.read_resource("backtest360://catalog/sections")
    payload = json.loads(list(blocks)[0].content)
    assert payload["status"] == 503
    assert "error" in payload


# ---------------------------------------------------------------------------
# the no-AI invariant
# ---------------------------------------------------------------------------

_LLM_PACKAGES = ("openai", "tiktoken", "transformers", "litellm", "langchain")


def test_no_llm_dependency_declared():
    """The adapter must stay AI-free: no LLM provider in the dependency tree."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text().lower()
    for pkg in _LLM_PACKAGES:
        assert pkg not in text, f"LLM package {pkg!r} found in pyproject.toml"


def test_no_llm_module_imported():
    import backtest360_mcp  # noqa: F401 — ensure the package is loaded

    loaded = {m.split(".")[0] for m in sys.modules}
    assert not loaded.intersection(_LLM_PACKAGES)
