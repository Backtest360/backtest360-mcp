"""Response shaping — projections of engine results sized for an LLM context.

A full engine backtest response is 1–5 MB; handing that to a model wastes (or
overflows) its context. Shaping selects and thins what the engine returned.
It is projection only: nothing here computes, derives, or interprets a number.

Detail levels for ``run_backtest`` / ``compare_backtests``:

- ``summary`` (default): headline metrics + warnings + counts + equity
  endpoints. Ends with a pointer to the deeper levels.
- ``stats``: every metric the engine returned; still no series or trades.
- ``full``: everything, with bar-indexed series downsampled and trades
  paginated. Oversized results are reduced further and marked
  ``truncated_by_mcp`` — never silently cut.
"""

from __future__ import annotations

import json
from typing import Any

# The headline metrics returned by the 'summary' projection — the high-signal
# performance numbers most callers want first, referenced by their stable
# snake_case metric ids (requested from the engine via stats_keys="ids", never
# by display label). The 'stats' and 'full' levels return every metric the
# engine provided.
HEADLINE_METRICS: tuple[str, ...] = (
    "total_return",
    "cagr",
    "ytd",
    "return_1y",
    "vol_ann",
    "sharpe",
    "sortino",
    "probabilistic_sharpe",
    "calmar",
    "max_drawdown",
    "max_dd_length",
    "worst_day",
    "var_5pc",
    "time_in_market_pct",
    "total_trades",
    "trade_win_rate",
    "win_rate",
    "avg_trade_pnl",
    "avg_holding_days",
    "best_trade",
    "profit_factor",
)

_DETAIL_LEVELS = ("summary", "stats", "full")
# Public: the add-on blocks run_backtest's ``include`` accepts. Exported so the
# tool can validate ``include`` before spending a quota-counted backtest.
INCLUDE_OPTIONS = (
    "trades",
    "equity_curve",
    "monthly_returns",
    "yearly_returns",
    "signal_diagnostics",
)

_DEFAULT_SERIES_POINTS = 500
_DEFAULT_TRADES_LIMIT = 50
_DEFAULT_FIRES_LIMIT = 200

# run_backtest accepts ``include``; compare_backtests does not — each tool gets
# a hint that names only the parameters it actually has.
_MORE_HINT = (
    "This is the '{detail}' projection. Re-run with response_detail='stats' for "
    "all metrics, response_detail='full' for series and trades, or pass "
    "include=['trades','equity_curve',...] to add specific blocks."
)
_COMPARE_MORE_HINT = (
    "This is the '{detail}' projection. Re-run with response_detail='stats' for "
    "all metrics, or response_detail='full' for series and trades (trades_limit "
    "caps how many trades each strategy returns)."
)


def _stride_indices(n: int, max_points: int) -> list[int]:
    """Evenly spaced indices over range(n), always keeping first and last."""
    if n <= max_points:
        return list(range(n))
    step = (n - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)})
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _downsample_block(block: dict[str, Any], max_points: int) -> dict[str, Any]:
    """Downsample a parallel-array block (dates + value arrays) by stride.

    Arrays sharing the block's bar count are thinned on the same indices so
    they stay aligned; everything else passes through untouched.
    """
    dates = block.get("dates")
    if not isinstance(dates, list) or len(dates) <= max_points:
        return block
    idx = _stride_indices(len(dates), max_points)
    n = len(dates)
    out: dict[str, Any] = {}
    for key, val in block.items():
        if isinstance(val, list) and len(val) == n:
            out[key] = [val[i] for i in idx]
        else:
            out[key] = val
    out["downsampled_from_bars"] = n
    out["points_returned"] = len(idx)
    return out


def _bar_count(result: dict[str, Any]) -> int | None:
    series = result.get("series")
    if isinstance(series, dict) and isinstance(series.get("dates"), list):
        return len(series["dates"])
    return None


def _base(result: dict[str, Any], detail: str) -> dict[str, Any]:
    """Fields present at every detail level."""
    shaped: dict[str, Any] = {"response_detail": detail}
    for key in ("created_at", "symbol", "signal_bars_per_year"):
        if result.get(key) is not None:
            shaped[key] = result[key]
    bars = _bar_count(result)
    trades = result.get("trades")
    shaped["counts"] = {
        "bars": bars,
        "trades": len(trades) if isinstance(trades, list) else None,
    }
    if result.get("warnings"):
        shaped["warnings"] = result["warnings"]
    if result.get("markers"):
        shaped["markers"] = result["markers"]
    if result.get("data_quality"):
        shaped["data_quality"] = result["data_quality"]
    if result.get("off_anchors"):
        shaped["off_anchors"] = result["off_anchors"]
    if result.get("benchmark_relative"):
        shaped["benchmark_relative"] = result["benchmark_relative"]
    if result.get("alignment"):
        shaped["alignment"] = result["alignment"]
    return shaped


def merge_leg_result(leg: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Flatten one leg of a backtest-envelope response into the ``result``
    shape the shaping functions expect.

    The engine's per-leg ``result`` no longer carries ``created_at``,
    ``symbol``, or ``warnings`` directly — those now live on the envelope's
    ``run`` block, the leg wrapper, and ``stats["_warnings"]`` respectively.
    This relocates them back so shaping can stay agnostic of the envelope.
    """
    result = dict(leg.get("result") or {})
    result["created_at"] = run.get("created_at")
    result["symbol"] = leg.get("symbol")
    result.setdefault("warnings", (result.get("stats") or {}).get("_warnings"))
    relative = leg.get("relative")
    if relative:
        result["benchmark_relative"] = relative
    alignment = run.get("alignment")
    if alignment:
        result["alignment"] = alignment
    return result


def _equity_endpoints(result: dict[str, Any]) -> dict[str, Any] | None:
    series = result.get("series")
    if not isinstance(series, dict):
        return None
    eq = series.get("strategy_equity")
    if not isinstance(eq, list) or not eq:
        return None
    finite = [v for v in eq if isinstance(v, (int, float))]
    if not finite:
        return None
    return {
        "start": eq[0],
        "end": eq[-1],
        "min": min(finite),
        "max": max(finite),
    }


def _trades_block(
    result: dict[str, Any], trades_limit: int
) -> dict[str, Any] | None:
    trades = result.get("trades")
    if not isinstance(trades, list):
        return None
    return {
        "trades": trades[:trades_limit],
        "trades_returned": min(len(trades), trades_limit),
        "trades_total": len(trades),
    }


def _equity_curve_block(
    result: dict[str, Any], max_points: int
) -> dict[str, Any] | None:
    series = result.get("series")
    if not isinstance(series, dict) or not series.get("dates"):
        return None
    keep = {
        k: series[k]
        for k in ("dates", "strategy_equity", "benchmark_equity", "drawdown")
        if isinstance(series.get(k), list)
    }
    return _downsample_block(keep, max_points)


def _signal_diagnostics_block(
    result: dict[str, Any], max_fires: int = _DEFAULT_FIRES_LIMIT
) -> dict[str, Any]:
    """Project ``signal_diagnostics`` to fire dates instead of per-bar flags.

    Each ``*_fired`` array is one boolean per bar; downsampling it by stride
    (as series are thinned) would silently drop fire events that fall between
    kept indices. Instead, each array is reduced to the dates (from the
    result's own series) on which it fired, most recent first, capped at
    ``max_fires`` — with an explicit ``fires_returned``/``fires_total`` pair
    per condition, mirroring the ``trades_returned``/``trades_total``
    convention. A result with no ``signal_diagnostics`` (e.g. a
    precomputed-signals run, which has no condition tree to evaluate) reports
    ``available: false`` with a reason rather than omitting the block
    silently — silent omission is only for a block that wasn't requested.
    """
    diagnostics = result.get("signal_diagnostics")
    if not isinstance(diagnostics, dict) or not diagnostics:
        return {
            "available": False,
            "reason": "Engine result carries no signal_diagnostics — likely a "
            "precomputed-signals run, which has no condition tree to evaluate.",
        }
    series = result.get("series")
    dates = series.get("dates") if isinstance(series, dict) else None
    if not isinstance(dates, list):
        return {
            "available": False,
            "reason": "signal_diagnostics present but the result has no "
            "series dates to align fire events against.",
        }
    n = len(dates)
    conditions: dict[str, Any] = {}
    for key, flags in diagnostics.items():
        if not (isinstance(flags, list) and len(flags) == n):
            continue
        fire_dates = [dates[i] for i, fired in enumerate(flags) if fired]
        total = len(fire_dates)
        conditions[key] = {
            "fire_dates": fire_dates[-max_fires:] if total > max_fires else fire_dates,
            "fires_returned": min(total, max_fires),
            "fires_total": total,
        }
    if not conditions:
        return {
            "available": False,
            "reason": "signal_diagnostics present but had no per-bar boolean "
            "arrays aligned to the result's series dates.",
        }
    return {"available": True, "conditions": conditions}


def shape_backtest_result(
    result: dict[str, Any],
    *,
    detail: str = "summary",
    include: list[str] | None = None,
    trades_limit: int = _DEFAULT_TRADES_LIMIT,
    max_output_bytes: int = 100_000,
    series_points: int = _DEFAULT_SERIES_POINTS,
    more_hint: str = _MORE_HINT,
) -> dict[str, Any]:
    """Project a full engine backtest ``result`` dict to the requested detail.

    ``more_hint`` is the deeper-detail pointer added at 'summary' level; it
    defaults to run_backtest's (which mentions ``include``) and is overridden
    for compare_backtests, whose signature has no ``include``.

    Raises:
        ValueError: On an unknown ``detail`` or ``include`` entry (reported to
            the agent verbatim so it can correct the call).
    """
    if detail not in _DETAIL_LEVELS:
        raise ValueError(
            f"Unknown response_detail {detail!r}. Use one of {_DETAIL_LEVELS}."
        )
    include = include or []
    bad = [i for i in include if i not in INCLUDE_OPTIONS]
    if bad:
        raise ValueError(
            f"Unknown include option(s) {bad}. Use any of {INCLUDE_OPTIONS}."
        )

    shaped = _base(result, detail)
    stats = result.get("stats") or {}

    if detail == "summary":
        shaped["stats"] = {k: stats[k] for k in HEADLINE_METRICS if k in stats}
        eq = _equity_endpoints(result)
        if eq:
            shaped["equity"] = eq
        shaped["more"] = more_hint.format(detail=detail)
    else:
        shaped["stats"] = stats

    if detail == "full":
        series = result.get("series")
        if isinstance(series, dict):
            shaped["series"] = _downsample_block(series, series_points)
        tb = _trades_block(result, trades_limit)
        if tb:
            shaped.update(tb)
        for key in ("monthly_returns", "yearly_returns"):
            if result.get(key):
                shaped[key] = result[key]
        rolling = result.get("rolling_statistics")
        if isinstance(rolling, dict):
            shaped["rolling_statistics"] = _downsample_block(rolling, series_points)
        include_signal_diag = "signal_diagnostics" in include
        omitted = []
        if result.get("results_df") is not None:
            omitted.append("results_df")
        if include_signal_diag:
            shaped["signal_diagnostics"] = _signal_diagnostics_block(result)
        elif result.get("signal_diagnostics") is not None:
            omitted.append("signal_diagnostics")
        if omitted:
            shaped["omitted_blocks"] = omitted
    else:
        # Optional add-on blocks at summary/stats level.
        if "signal_diagnostics" in include:
            shaped["signal_diagnostics"] = _signal_diagnostics_block(result)
        if "trades" in include:
            tb = _trades_block(result, trades_limit)
            if tb:
                shaped.update(tb)
        if "equity_curve" in include:
            ec = _equity_curve_block(result, series_points)
            if ec:
                shaped["equity_curve"] = ec
        for key in ("monthly_returns", "yearly_returns"):
            if key in include and result.get(key):
                shaped[key] = result[key]

    return _enforce_cap(shaped, max_output_bytes)


def shape_compare_result(
    resp: dict[str, Any],
    *,
    label_by_id: dict[str, str],
    detail: str = "summary",
    trades_limit: int = _DEFAULT_TRADES_LIMIT,
    max_output_bytes: int = 100_000,
    series_points: int = _DEFAULT_SERIES_POINTS,
) -> dict[str, Any]:
    """Project a ``POST /api/backtest`` ``{status, run, legs}`` envelope for
    a multi-strategy comparison.

    Each leg's result is flattened via :func:`merge_leg_result` and shaped at
    the requested detail; the combined equity-curve block (one strategy_equity
    series per leg, keyed by its caller-supplied label) is downsampled.
    """
    run = resp.get("run") or {}
    legs = resp.get("legs")
    shaped: dict[str, Any] = {}
    curves: dict[str, Any] = {}
    if isinstance(legs, list):
        entries = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            leg_id = leg.get("id")
            label = label_by_id.get(leg_id, leg_id)
            result = merge_leg_result(leg, run)
            shaped_result = shape_backtest_result(
                result,
                detail=detail,
                trades_limit=trades_limit,
                # Per-strategy cap is enforced by the outer cap below.
                max_output_bytes=max_output_bytes,
                series_points=series_points,
                # compare_backtests has no ``include`` — don't advertise it.
                more_hint=_COMPARE_MORE_HINT,
            )
            entry: dict[str, Any] = {"label": label, "result": shaped_result}
            relative = leg.get("relative")
            if relative:
                entry["relative"] = relative
            entries.append(entry)

            series = result.get("series")
            if isinstance(series, dict) and isinstance(series.get("dates"), list):
                if "dates" not in curves:
                    curves["dates"] = series["dates"]
                eq = series.get("strategy_equity")
                if isinstance(eq, list):
                    curves[label] = eq
        shaped["strategies"] = entries

    if curves:
        shaped["equity_curves"] = _downsample_block(curves, series_points)

    alignment = run.get("alignment")
    if isinstance(alignment, dict):
        shaped["alignment"] = {
            k: alignment[k]
            for k in ("shared_bars", "effective_start", "effective_end")
            if k in alignment
        }

    return _enforce_cap(shaped, max_output_bytes)


def cap_passthrough_list(
    payload: dict[str, Any],
    *,
    list_key: str,
    max_items: int,
    more: str,
    max_output_bytes: int = 100_000,
) -> dict[str, Any]:
    """Bound a passthrough list response so a large engine payload cannot
    overflow a model's context or break the MCP transport.

    A discovery endpoint (e.g. the full ticker universe) can return tens of MB —
    enough to crash a stdio client. This thins ``payload[list_key]`` to
    ``max_items`` rows (and halves further if those rows still exceed
    ``max_output_bytes``), never silently: it records ``returned`` (rows kept),
    ``total`` (rows the engine returned), a ``truncated_by_mcp`` marker, and a
    ``more`` hint pointing at the narrower call. Payloads already within both
    bounds are returned unchanged. Projection only — nothing is computed.
    """
    items = payload.get(list_key)
    if not isinstance(items, list):
        return payload

    total = len(items)
    kept = items[:max_items]
    capped = len(kept) < total

    shaped = {**payload, list_key: kept}
    # Byte-ceiling backstop: even ``max_items`` rows could exceed the cap.
    while _size(shaped) > max_output_bytes and len(kept) > 1:
        kept = kept[: len(kept) // 2]
        shaped = {**payload, list_key: kept}
        capped = True

    if not capped:
        return payload

    shaped["returned"] = len(kept)
    shaped["total"] = total
    shaped["truncated_by_mcp"] = True
    shaped["more"] = more
    return shaped


def cap_output_size(
    payload: dict[str, Any],
    *,
    more: str,
    max_output_bytes: int = 100_000,
) -> dict[str, Any]:
    """Transport-safety backstop for a passthrough dict of varying shape.

    Reference catalogs are small and the caller usually needs them whole, so
    this only acts when the serialized ``payload`` exceeds ``max_output_bytes``
    — then it halves the largest list-valued field(s) until it fits and marks
    ``truncated_by_mcp``. Bounded payloads (and any without a list to thin) pass
    through unchanged. Projection only — nothing is computed.
    """
    if not isinstance(payload, dict) or _size(payload) <= max_output_bytes:
        return payload

    out = dict(payload)
    thinned = False
    while _size(out) > max_output_bytes:
        list_keys = [k for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not list_keys:
            break
        biggest = max(list_keys, key=lambda k: len(out[k]))
        out[biggest] = out[biggest][: len(out[biggest]) // 2]
        thinned = True

    if not thinned:
        return payload  # nothing to safely thin — leave it intact

    out["truncated_by_mcp"] = True
    out["more"] = more
    return out


def shape_series_response(
    payload: dict[str, Any],
    *,
    block_key: str,
    more: str,
    max_points: int = _DEFAULT_SERIES_POINTS,
    max_output_bytes: int = 100_000,
) -> dict[str, Any]:
    """Project a research response whose bulk is one nested parallel-array block.

    A price-history or macro-observations response is a small metadata envelope
    plus one block of date-aligned arrays (``block_key`` -> {dates, ...columns})
    that can run to thousands of rows. This downsamples that block by stride to
    ``max_points`` — first and last row always kept, every column thinned on the
    same indices so they stay aligned — recording ``downsampled_from_bars`` and
    ``points_returned`` on the block. If the result still exceeds
    ``max_output_bytes`` it thins the block harder and marks ``truncated_by_mcp``.
    A block already within ``max_points`` (and the byte ceiling) passes through
    untouched, unmarked. Projection only — no value is computed or altered.
    """
    if not isinstance(payload, dict):
        return payload

    block = payload.get(block_key)
    if not (isinstance(block, dict) and isinstance(block.get("dates"), list)):
        # No parallel-array block to thin — still guard total size defensively.
        return cap_output_size(payload, more=more, max_output_bytes=max_output_bytes)

    # The engine's own row arrays, stripped of any provenance markers so a
    # re-thin recomputes them from the full block rather than a thinned one.
    raw_block = {
        k: v
        for k, v in block.items()
        if k not in ("downsampled_from_bars", "points_returned")
    }
    shaped = dict(payload)
    points = max_points
    shaped[block_key] = _downsample_block(raw_block, points)
    while _size(shaped) > max_output_bytes and points > 50:
        points //= 2
        shaped[block_key] = _downsample_block(raw_block, points)
    if _size(shaped) > max_output_bytes:
        shaped["truncated_by_mcp"] = True
        shaped["more"] = more
    return shaped


def _enforce_cap(shaped: dict[str, Any], max_output_bytes: int) -> dict[str, Any]:
    """Hard size cap: progressively reduce, then mark — never silently cut.

    Reduction order: thin series harder → trim trades → drop series blocks.
    Whatever was reduced is recorded under ``truncated_by_mcp``.
    """
    if _size(shaped) <= max_output_bytes:
        return shaped

    reductions: list[str] = []
    for points in (250, 100):
        for key in ("series", "equity_curve", "rolling_statistics", "equity_curves"):
            block = shaped.get(key)
            if isinstance(block, dict):
                shaped[key] = _downsample_block(
                    {k: v for k, v in block.items()
                     if k not in ("downsampled_from_bars", "points_returned")},
                    points,
                )
        reductions.append(f"series thinned to {points} points")
        if _size(shaped) <= max_output_bytes:
            break

    if _size(shaped) > max_output_bytes and isinstance(shaped.get("trades"), list):
        shaped["trades"] = shaped["trades"][:10]
        shaped["trades_returned"] = len(shaped["trades"])
        reductions.append("trades trimmed to 10")

    if _size(shaped) > max_output_bytes:
        for key in ("series", "equity_curve", "rolling_statistics", "equity_curves"):
            if key in shaped:
                del shaped[key]
                reductions.append(f"{key} dropped")
        if _size(shaped) > max_output_bytes and isinstance(shaped.get("trades"), list):
            del shaped["trades"]
            reductions.append("trades dropped")

    shaped["truncated_by_mcp"] = True
    shaped["truncation_applied"] = reductions
    return shaped


def _size(obj: dict[str, Any]) -> int:
    return len(json.dumps(obj, default=str))
