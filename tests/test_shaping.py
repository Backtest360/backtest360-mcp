"""Shaping projections: detail levels, downsampling, includes, the hard cap."""

from __future__ import annotations

import json

import pytest

from backtest360_mcp.shaping import (
    HEADLINE_METRICS,
    INCLUDE_OPTIONS,
    cap_output_size,
    cap_passthrough_list,
    merge_leg_result,
    shape_backtest_result,
    shape_compare_result,
    shape_series_response,
)
from conftest import N_BARS, N_TRADES, _dates, make_backtest_result

_CREATED_AT = "2026-06-12T00:00:00+00:00"


@pytest.fixture
def result():
    """A flattened result as tools/backtest.py hands it to shape_backtest_result
    — a raw per-leg result merged with its envelope's run/leg-level fields."""
    leg = {"id": "strategy", "symbol": "TEST", "result": make_backtest_result()}
    return merge_leg_result(leg, {"created_at": _CREATED_AT})


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_has_headline_stats_only(result):
    shaped = shape_backtest_result(result, detail="summary")
    assert set(shaped["stats"]) == set(HEADLINE_METRICS)
    assert "Omega" not in shaped["stats"]  # paid extra not in headline set


def test_summary_has_counts_equity_warnings_markers(result):
    shaped = shape_backtest_result(result, detail="summary")
    assert shaped["counts"] == {"bars": N_BARS, "trades": N_TRADES}
    assert shaped["equity"]["start"] == 1.0
    assert shaped["warnings"][0]["code"] == "ZERO_COSTS_CONFIGURED"
    assert shaped["markers"]["warmup_bars"] == 14
    assert "more" in shaped


def test_summary_excludes_series_and_trades(result):
    shaped = shape_backtest_result(result, detail="summary")
    assert "series" not in shaped
    assert "trades" not in shaped


def test_summary_is_small(result):
    shaped = shape_backtest_result(result, detail="summary")
    assert len(json.dumps(shaped)) < 8_000


def test_summary_values_are_verbatim(result):
    shaped = shape_backtest_result(result, detail="summary")
    for key, value in shaped["stats"].items():
        assert value == result["stats"][key]  # projection only — never derived


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def test_stats_returns_all_metrics(result):
    shaped = shape_backtest_result(result, detail="stats")
    assert set(shaped["stats"]) == set(result["stats"])
    assert "series" not in shaped


# ---------------------------------------------------------------------------
# full
# ---------------------------------------------------------------------------

def test_full_downsamples_series_aligned(result):
    shaped = shape_backtest_result(result, detail="full")
    series = shaped["series"]
    n = series["points_returned"]
    assert series["downsampled_from_bars"] == N_BARS
    assert n <= 501
    # All bar-length arrays thinned to the same length — alignment preserved.
    for key in ("dates", "returns", "strategy_equity", "signals"):
        assert len(series[key]) == n
    # First and last bars always kept.
    assert series["dates"][0] == result["series"]["dates"][0]
    assert series["dates"][-1] == result["series"]["dates"][-1]


def test_full_paginates_trades(result):
    shaped = shape_backtest_result(result, detail="full", trades_limit=25)
    assert shaped["trades_returned"] == 25
    assert shaped["trades_total"] == N_TRADES
    assert len(shaped["trades"]) == 25


def test_full_omits_per_bar_table_explicitly(result):
    shaped = shape_backtest_result(result, detail="full")
    assert "results_df" not in shaped
    assert "results_df" in shaped["omitted_blocks"]


def test_no_downsampling_when_small():
    result = make_backtest_result(n_bars=100, n_trades=3)
    shaped = shape_backtest_result(result, detail="full")
    assert len(shaped["series"]["dates"]) == 100
    assert "downsampled_from_bars" not in shaped["series"]


# ---------------------------------------------------------------------------
# signal_bars_per_year
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("detail", ["summary", "stats", "full"])
def test_signal_bars_per_year_passed_through_when_present(detail):
    leg = {
        "id": "strategy", "symbol": "TEST",
        "result": {**make_backtest_result(), "signal_bars_per_year": 252},
    }
    result = merge_leg_result(leg, {"created_at": _CREATED_AT})
    shaped = shape_backtest_result(result, detail=detail)
    assert shaped["signal_bars_per_year"] == 252


@pytest.mark.parametrize("detail", ["summary", "stats", "full"])
def test_signal_bars_per_year_absent_when_engine_omits_it(result, detail):
    # Older engine response — make_backtest_result() carries no such field.
    shaped = shape_backtest_result(result, detail=detail)
    assert "signal_bars_per_year" not in shaped


# ---------------------------------------------------------------------------
# include add-ons
# ---------------------------------------------------------------------------

def test_include_trades_at_summary(result):
    shaped = shape_backtest_result(result, detail="summary", include=["trades"])
    assert shaped["trades_returned"] == 50
    assert shaped["trades_total"] == N_TRADES


def test_include_equity_curve_at_stats(result):
    shaped = shape_backtest_result(result, detail="stats", include=["equity_curve"])
    curve = shaped["equity_curve"]
    assert "strategy_equity" in curve
    assert len(curve["dates"]) <= 501


def test_unknown_detail_rejected(result):
    with pytest.raises(ValueError, match="response_detail"):
        shape_backtest_result(result, detail="everything")


def test_unknown_include_rejected(result):
    with pytest.raises(ValueError, match="include"):
        shape_backtest_result(result, include=["alpha_curve"])


# ---------------------------------------------------------------------------
# signal_diagnostics
# ---------------------------------------------------------------------------

def _result_with_fires(n_fires, n_bars=300):
    """A result whose last ``n_fires`` bars fired ``long_entry_fired``."""
    fixture = make_backtest_result(n_bars=n_bars, n_trades=1)
    fired = [False] * n_bars
    for i in range(n_bars - n_fires, n_bars):
        fired[i] = True
    fixture["signal_diagnostics"] = {
        "long_entry_fired": fired,
        "long_exit_fired": [False] * n_bars,
    }
    return fixture


def test_signal_diagnostics_is_a_valid_include_option():
    assert "signal_diagnostics" in INCLUDE_OPTIONS


def test_signal_diagnostics_not_requested_full_lists_it_omitted(result):
    shaped = shape_backtest_result(result, detail="full")
    assert "signal_diagnostics" not in shaped
    assert shaped["omitted_blocks"] == ["results_df", "signal_diagnostics"]


def test_signal_diagnostics_not_requested_absent_at_summary_and_stats(result):
    for detail in ("summary", "stats"):
        shaped = shape_backtest_result(result, detail=detail)
        assert "signal_diagnostics" not in shaped


def test_signal_diagnostics_included_returns_fire_dates_not_booleans():
    fixture = _result_with_fires(n_fires=5, n_bars=50)
    shaped = shape_backtest_result(
        fixture, detail="full", include=["signal_diagnostics"]
    )
    diag = shaped["signal_diagnostics"]
    assert diag["available"] is True
    long_entry = diag["conditions"]["long_entry_fired"]
    assert long_entry["fires_total"] == 5
    assert long_entry["fires_returned"] == 5
    expected_dates = fixture["series"]["dates"][45:50]
    assert long_entry["fire_dates"] == expected_dates
    # A condition present but never fired still appears, with zero fires.
    assert diag["conditions"]["long_exit_fired"]["fires_total"] == 0
    assert diag["conditions"]["long_exit_fired"]["fire_dates"] == []
    # Requested and satisfied — not also listed as omitted.
    assert "signal_diagnostics" not in shaped.get("omitted_blocks", [])
    # results_df is still unrequested and still omitted.
    assert shaped["omitted_blocks"] == ["results_df"]


def test_signal_diagnostics_caps_to_most_recent_200_fires():
    fixture = _result_with_fires(n_fires=250, n_bars=400)
    shaped = shape_backtest_result(
        fixture, detail="full", include=["signal_diagnostics"]
    )
    long_entry = shaped["signal_diagnostics"]["conditions"]["long_entry_fired"]
    assert long_entry["fires_total"] == 250
    assert long_entry["fires_returned"] == 200
    assert len(long_entry["fire_dates"]) == 200
    all_fire_dates = fixture["series"]["dates"][400 - 250 :]
    assert long_entry["fire_dates"] == all_fire_dates[-200:]


def test_signal_diagnostics_available_false_when_engine_omits_it(result):
    result["signal_diagnostics"] = None  # precomputed-signals run
    shaped = shape_backtest_result(
        result, detail="full", include=["signal_diagnostics"]
    )
    diag = shaped["signal_diagnostics"]
    assert diag["available"] is False
    assert diag["reason"]
    assert "signal_diagnostics" not in shaped.get("omitted_blocks", [])


def test_signal_diagnostics_included_at_summary_and_stats():
    fixture = _result_with_fires(n_fires=3, n_bars=30)
    for detail in ("summary", "stats"):
        shaped = shape_backtest_result(
            fixture, detail=detail, include=["signal_diagnostics"]
        )
        assert shaped["signal_diagnostics"]["available"] is True
        conditions = shaped["signal_diagnostics"]["conditions"]
        assert conditions["long_entry_fired"]["fires_total"] == 3


# ---------------------------------------------------------------------------
# hard cap
# ---------------------------------------------------------------------------

def test_cap_marks_truncation_explicitly(result):
    shaped = shape_backtest_result(result, detail="full", max_output_bytes=5_000)
    assert shaped["truncated_by_mcp"] is True
    assert shaped["truncation_applied"]
    assert len(json.dumps(shaped, default=str)) < 50_000


def test_cap_keeps_stats_intact(result):
    shaped = shape_backtest_result(result, detail="full", max_output_bytes=5_000)
    # Reduction drops bulk arrays, never the numbers the agent reasons over.
    assert set(shaped["stats"]) == set(result["stats"])


def test_no_truncation_marker_when_under_cap(result):
    shaped = shape_backtest_result(result, detail="summary")
    assert "truncated_by_mcp" not in shaped


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def test_compare_shapes_each_strategy():
    dates = _dates(N_BARS)
    resp = {
        "run": {
            "reference": None,
            "alignment": {
                "shared_bars": N_BARS,
                "effective_start": dates[0],
                "effective_end": dates[-1],
            },
            "created_at": _CREATED_AT,
        },
        "legs": [
            {"id": "a", "symbol": "TEST", "result": make_backtest_result()},
            {"id": "b", "symbol": "TEST", "result": make_backtest_result()},
        ],
    }
    shaped = shape_compare_result(
        resp, label_by_id={"a": "A", "b": "B"}, detail="summary"
    )
    assert [s["label"] for s in shaped["strategies"]] == ["A", "B"]
    assert set(shaped["strategies"][0]["result"]["stats"]) == set(HEADLINE_METRICS)
    assert len(shaped["equity_curves"]["dates"]) <= 501
    assert shaped["equity_curves"]["A"] and shaped["equity_curves"]["B"]
    assert shaped["alignment"]["shared_bars"] == N_BARS


def test_compare_surfaces_relative_metrics_when_present():
    relative = {"beta": 0.9, "alpha": 0.02, "information_ratio": 0.5}
    resp = {
        "run": {"reference": "benchmark", "alignment": {}, "created_at": _CREATED_AT},
        "legs": [
            {"id": "a", "symbol": "TEST", "result": make_backtest_result(),
             "relative": relative},
            {"id": "benchmark", "symbol": "TEST", "result": make_backtest_result()},
        ],
    }
    shaped = shape_compare_result(
        resp, label_by_id={"a": "A", "benchmark": "Benchmark"}, detail="summary"
    )
    strat_a = next(s for s in shaped["strategies"] if s["label"] == "A")
    strat_bench = next(s for s in shaped["strategies"] if s["label"] == "Benchmark")
    assert strat_a["relative"] == relative
    assert "relative" not in strat_bench


# ---------------------------------------------------------------------------
# cap_passthrough_list — bound large discovery responses
# ---------------------------------------------------------------------------

def test_cap_passthrough_list_caps_huge_list():
    payload = {
        "status": "success",
        "count": 141_124,
        "results": [{"ticker": f"T{i}", "name": f"name {i}"} for i in range(141_124)],
    }
    out = cap_passthrough_list(
        payload, list_key="results", max_items=200, more="narrow it"
    )
    assert len(out["results"]) == 200
    assert out["returned"] == 200
    assert out["total"] == 141_124
    assert out["truncated_by_mcp"] is True
    assert out["more"] == "narrow it"
    # The engine's own total count is preserved untouched.
    assert out["count"] == 141_124
    # The source payload is not mutated.
    assert len(payload["results"]) == 141_124


def test_cap_passthrough_list_leaves_small_list_untouched():
    payload = {"status": "success", "count": 2, "results": [{"ticker": "A"}, {"ticker": "B"}]}
    out = cap_passthrough_list(
        payload, list_key="results", max_items=200, more="narrow it"
    )
    assert out is payload  # unchanged identity — no marker added
    assert "truncated_by_mcp" not in out


def test_cap_passthrough_list_byte_ceiling_backstop():
    # Rows fat enough that even <max_items of them blow the byte ceiling.
    payload = {"results": [{"blob": "x" * 5_000} for _ in range(50)]}
    out = cap_passthrough_list(
        payload, list_key="results", max_items=200, more="narrow it",
        max_output_bytes=20_000,
    )
    assert out["truncated_by_mcp"] is True
    assert out["total"] == 50
    assert json.dumps(out) and len(json.dumps(out)) <= 20_000 + len(json.dumps(
        {"returned": 0, "total": 50, "truncated_by_mcp": True, "more": "narrow it"}
    ))


def test_cap_passthrough_list_ignores_non_list():
    payload = {"results": {"not": "a list"}}
    assert cap_passthrough_list(payload, list_key="results", max_items=5, more="x") is payload


# ---------------------------------------------------------------------------
# cap_output_size — transport backstop for varying-shape passthrough dicts
# ---------------------------------------------------------------------------

def test_cap_output_size_thins_largest_list_when_over_ceiling():
    payload = {
        "sections": ["s"] * 5,
        "metrics": [{"id": f"m{i}", "blob": "x" * 500} for i in range(200)],
    }
    out = cap_output_size(payload, more="too big", max_output_bytes=20_000)
    assert out["truncated_by_mcp"] is True
    assert out["more"] == "too big"
    assert len(json.dumps(out)) <= 20_000
    assert len(out["metrics"]) < 200  # the big list was thinned
    assert out["sections"] == ["s"] * 5  # the small one left alone


def test_cap_output_size_leaves_small_payload_untouched():
    payload = {"operators": [{"id": "gt"}], "note": "small"}
    out = cap_output_size(payload, more="x", max_output_bytes=100_000)
    assert out is payload
    assert "truncated_by_mcp" not in out


def test_cap_output_size_leaves_unthinnable_payload_intact():
    # Over the ceiling but no list to thin — return as-is rather than mislabel.
    payload = {"blob": "x" * 50_000}
    out = cap_output_size(payload, more="x", max_output_bytes=10_000)
    assert out is payload
    assert "truncated_by_mcp" not in out


def test_cap_output_size_ignores_non_dict():
    assert cap_output_size(["a", "b"], more="x", max_output_bytes=1) == ["a", "b"]


# ---------------------------------------------------------------------------
# shape_series_response — research price-history / macro-observations blocks
# ---------------------------------------------------------------------------

def _series_payload(block_key: str, n: int) -> dict:
    dates = _dates(n)  # unique ISO dates, one per row
    return {
        "status": "success",
        "summary": {"total_bars": n},
        block_key: {
            "dates": dates,
            "open": [float(i) for i in range(n)],
            "close": [float(i) + 0.5 for i in range(n)],
        },
    }


def test_series_downsamples_and_marks_provenance():
    payload = _series_payload("ohlcv", 1000)
    out = shape_series_response(payload, block_key="ohlcv", more="x", max_points=500)
    block = out["ohlcv"]
    assert block["downsampled_from_bars"] == 1000
    assert block["points_returned"] <= 500
    # First and last row always kept; every column thinned on the same indices.
    assert block["dates"][0] == payload["ohlcv"]["dates"][0]
    assert block["dates"][-1] == payload["ohlcv"]["dates"][-1]
    assert len(block["open"]) == len(block["close"]) == len(block["dates"])
    # The metadata envelope keeps the true count — no marker for expected thinning.
    assert out["summary"]["total_bars"] == 1000
    assert "truncated_by_mcp" not in out


def test_series_small_block_passes_through_unmarked():
    payload = _series_payload("observations", 10)
    out = shape_series_response(
        payload, block_key="observations", more="x", max_points=500
    )
    assert out["observations"] == payload["observations"]  # untouched
    assert "downsampled_from_bars" not in out["observations"]
    assert "truncated_by_mcp" not in out


def test_series_byte_cap_forces_further_thinning_and_marks():
    payload = _series_payload("ohlcv", 2000)
    out = shape_series_response(
        payload, block_key="ohlcv", more="reduced", max_points=500,
        max_output_bytes=500,
    )
    # Even at the thinning floor the block still exceeds the (tiny) cap, so the
    # result is explicitly marked rather than silently cut.
    assert out["truncated_by_mcp"] is True
    assert out["more"] == "reduced"
    assert out["ohlcv"]["points_returned"] < 500


def test_series_values_are_verbatim():
    payload = _series_payload("ohlcv", 300)
    out = shape_series_response(payload, block_key="ohlcv", more="x", max_points=100)
    block = out["ohlcv"]
    # Each kept value must equal the engine's value at that date — never derived.
    for date, close in zip(block["dates"], block["close"]):
        i = payload["ohlcv"]["dates"].index(date)
        assert close == payload["ohlcv"]["close"][i]


def test_series_no_block_falls_back_to_size_guard():
    payload = {"status": "success", "summary": {"total_bars": 0}}
    out = shape_series_response(payload, block_key="ohlcv", more="x")
    assert out == payload  # nothing to thin, within cap → unchanged
