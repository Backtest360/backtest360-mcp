"""Backtest execution tools."""

from __future__ import annotations

import base64
import re
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backtest360_mcp.engine_client import EngineClient, EngineError
from backtest360_mcp.settings import Settings
from backtest360_mcp.tools import engine_tool, fixable_result

Detail = Literal["summary", "stats", "full"]

_FIXABLE_HINT = (
    "The engine rejected the request as invalid. Fix the field(s) named in the "
    "error and retry. If the problem is in the strategy document, run "
    "validate_strategy first — it reports every issue with its exact location."
)

_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _fixable(exc: EngineError) -> dict[str, Any]:
    return fixable_result(exc, _FIXABLE_HINT)


def _slugify(label: str) -> str:
    """A stable, id-safe slug for use as a leg id (1..64 chars)."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return (slug or "leg")[:64]


def _run_overrides(
    start: str | None,
    end: str | None,
    signal_frequency: str | None,
) -> dict[str, Any]:
    """The ``run.start`` / ``run.end`` / ``run.signal_frequency`` run-level
    overrides shared by run_backtest / compare_backtests / export_backtest —
    each applies across every leg of the request (a leg's own
    ``data_source.start``/``.end`` or ``execution.signal_frequency`` is used
    only when the corresponding run-level override is omitted).
    """
    overrides: dict[str, Any] = {}
    if start is not None:
        overrides["start"] = start
    if end is not None:
        overrides["end"] = end
    if signal_frequency is not None:
        overrides["signal_frequency"] = signal_frequency
    return overrides


def _build_legs_body(
    data_source: dict[str, Any],
    strategies: list[dict[str, Any]],
    include_benchmark: bool,
    *,
    benchmark_data_source: dict[str, Any] | None = None,
    benchmark_exposure: float | None = None,
    reference_label: str | None = None,
    run_start: str | None = None,
    run_end: str | None = None,
    run_signal_frequency: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build the ``{run, legs}`` request body shared by compare_backtests and
    export_backtest, and the label lookup needed to translate leg ids back to
    the caller's labels in the response.

    ``benchmark_data_source`` (optional): use an independent data source for
    the added benchmark leg instead of the shared ``data_source`` — mirrors
    run_backtest's independent ``benchmark`` param. Its presence alone (like
    ``include_benchmark=True``) is enough to add the benchmark leg.
    ``reference_label`` (optional): resolve the ``run.reference`` baseline to
    any strategies[] entry by its caller-supplied label (or the literal
    string ``"benchmark"`` for the auto-added benchmark leg) instead of the
    engine's own benchmark-leg default.
    ``benchmark_exposure`` (optional): leverage knob forwarded onto the
    auto-added benchmark leg's ``exposure`` field. A strategies[] entry may
    also carry its own ``exposure``/``include_leaf_series`` — forwarded
    verbatim onto that leg (the engine, not this layer, enforces that
    ``exposure`` is only valid ≠ 1.0 on a benchmark leg).
    """
    add_benchmark_leg = include_benchmark or benchmark_data_source is not None
    used_ids: set[str] = {"benchmark"} if add_benchmark_leg else set()
    label_by_id: dict[str, str] = {}
    # None marks a label used by 2+ entries — ambiguous as a reference target.
    id_by_label: dict[str, str | None] = {}
    legs: list[dict[str, Any]] = []
    for entry in strategies:
        label = entry.get("label")
        strategy = entry.get("strategy")
        signals = entry.get("signals")
        if not label or (strategy is None and signals is None):
            raise ValueError(
                "Each strategies[] entry needs a non-empty 'label' and "
                "either a 'strategy' document or precomputed 'signals'; "
                f"got {entry!r}."
            )
        leg_id = _slugify(label)
        base_id = leg_id
        n = 2
        while leg_id in used_ids:
            suffix = f"-{n}"
            leg_id = f"{base_id[: 64 - len(suffix)]}{suffix}"
            n += 1
        used_ids.add(leg_id)
        label_by_id[leg_id] = label
        id_by_label[label] = leg_id if label not in id_by_label else None

        leg: dict[str, Any] = {"id": leg_id, "data_source": data_source}
        if strategy is not None:
            leg["strategy"] = strategy
        if signals is not None:
            leg["signals"] = signals
        if entry.get("execution") is not None:
            leg["execution"] = entry["execution"]
        if entry.get("data_inputs") is not None:
            leg["data_inputs"] = entry["data_inputs"]
        if entry.get("exposure") is not None:
            leg["exposure"] = entry["exposure"]
        if entry.get("include_leaf_series") is not None:
            leg["include_leaf_series"] = entry["include_leaf_series"]
        legs.append(leg)

    run: dict[str, Any] = {"stats_keys": "ids", "response_detail": "full"}
    if add_benchmark_leg:
        benchmark_leg: dict[str, Any] = {
            "id": "benchmark",
            "data_source": (
                benchmark_data_source if benchmark_data_source is not None
                else data_source
            ),
            "benchmark": True,
        }
        if benchmark_exposure is not None:
            benchmark_leg["exposure"] = benchmark_exposure
        legs.append(benchmark_leg)
        run["reference"] = "benchmark"

    if reference_label is not None:
        if reference_label == "benchmark" and add_benchmark_leg:
            run["reference"] = "benchmark"
        elif id_by_label.get(reference_label) is not None:
            run["reference"] = id_by_label[reference_label]
        elif reference_label in id_by_label:
            raise ValueError(
                f"reference_label {reference_label!r} matches more than one "
                "strategies[] entry — give the intended reference strategy "
                "a unique label."
            )
        else:
            raise ValueError(
                f"reference_label {reference_label!r} does not match any "
                f"strategies[] label ({sorted(label_by_id.values())!r}) or "
                "the auto-added 'benchmark' leg."
            )

    run.update(_run_overrides(run_start, run_end, run_signal_frequency))

    return {"run": run, "legs": legs}, label_by_id


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    from backtest360_mcp.shaping import (
        INCLUDE_OPTIONS,
        merge_leg_result,
        shape_backtest_result,
        shape_compare_result,
    )

    @mcp.tool()
    @engine_tool
    def run_backtest(
        data_source: dict[str, Any],
        strategy: dict[str, Any] | None = None,
        signals: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        benchmark: dict[str, Any] | None = None,
        benchmark_exposure: float | None = None,
        data_inputs: dict[str, Any] | None = None,
        include_leaf_series: bool = False,
        run_start: str | None = None,
        run_end: str | None = None,
        run_signal_frequency: str | None = None,
        response_detail: Detail = "summary",
        include: list[str] | None = None,
        trades_limit: int = 50,
        max_series_points: int | None = None,
    ) -> dict[str, Any]:
        """Run a historical backtest against the engine.

        Quota-counted and compute-bound. Validate the
        strategy first (validate_strategy is far cheaper). On a 504 compute
        timeout, do NOT retry the same request — reduce the date range, use a
        coarser frequency, or simplify the strategy. On 429/503, wait for the
        advertised Retry-After before retrying.

        Args:
            data_source: Either inline OHLCV ({"ohlcv": {dates, open, high,
                low, close, volume?}} as parallel arrays, ISO-8601 dates) or a
                server-side fetch ({"symbol", "start", "end", "frequency"} —
                requires a paid plan).
            strategy: Strategy document (indicators[] + condition_tree).
                Mutually exclusive with signals.
            signals: Precomputed signal series ({"dates": [...], "values":
                [-1|0|1, ...]}). Mutually exclusive with strategy.
            execution: Execution/cost/risk/sizing settings. Use values from
                get_catalog('execution-modes'/'stop-types'/'sizing-methods');
                omit for engine defaults.
            benchmark: Optional benchmark data source (same shape as
                data_source) — when given, the result also carries
                benchmark-relative metrics (beta, alpha, information ratio,
                tracking error, up/down capture) and bar-alignment info.
            benchmark_exposure: Leverage knob for the benchmark leg (default
                1.0, must be in (0, 10]). Only meaningful when benchmark is
                given — it is the engine's one caller-settable field on a
                benchmark leg (every other cost/stop/sizing field is forced
                to its zero-cost default there).
            data_inputs: Optional custom time-series the strategy references
                (name -> {dates, values}).
            include_leaf_series: Add a per-bar boolean 'series' array to each
                leaf of the strategy leg's signal_diagnostics (visible when
                response_detail='full' or include contains
                'signal_diagnostics'). Off by default — can add real payload
                weight on a long multi-year run.
            run_start: Override the strategy leg's (and, if present, the
                benchmark leg's) data_source.start. Only affects
                symbol-fetched legs; inline ohlcv legs are unaffected.
            run_end: Override the strategy/benchmark legs' data_source.end.
                Same scope as run_start.
            run_signal_frequency: Override the strategy/benchmark legs'
                execution.signal_frequency.
            response_detail: 'summary' (default — headline metrics, smallest),
                'stats' (every metric), 'full' (plus trades and series
                downsampled to a fixed, server-controlled number of points).
            include: Optional add-on blocks at any detail level: 'trades',
                'equity_curve', 'monthly_returns', 'yearly_returns',
                'signal_diagnostics' (which per-bar entry/exit conditions
                fired, as capped fire-date lists — {"available": false, ...}
                if the run has none, e.g. precomputed signals).
            trades_limit: Max trades returned when trades are included.
            max_series_points: Maximum number of points per returned series
                (default 500). Longer runs are downsampled to this cap; set
                higher to receive full-resolution series. Must be >= 2 when
                given.

        Returns:
            The shaped result at the requested detail (including
            ``benchmark_relative``/``alignment`` when a benchmark was given);
            an oversized result is thinned and marked ``truncated_by_mcp``. If
            the engine rejects the request as invalid (400/422), returns
            {"accepted": false, "error": ...} so you can fix the named
            field(s) and retry. Capacity, timeout, and permission failures
            (e.g. 429/503/504/401/403) raise a tool error carrying explicit
            recovery guidance.
        """
        # Validate include BEFORE the (quota-counted) run: a typo like
        # include=['equit'] would otherwise burn a full backtest, then error
        # only when shaping rejects it.
        bad_include = [i for i in (include or []) if i not in INCLUDE_OPTIONS]
        if bad_include:
            return {
                "accepted": False,
                "status": 422,
                "error": {"detail": {
                    "code": "INVALID_INCLUDE",
                    "message": (
                        f"Unknown include value(s) {bad_include}. "
                        f"Valid values: {list(INCLUDE_OPTIONS)}."
                    ),
                }},
                "hint": "Correct or drop the named include value(s) and retry — "
                        "no backtest ran, so this used no quota.",
            }
        if max_series_points is not None and max_series_points < 2:
            return {
                "accepted": False,
                "status": 422,
                "error": {"detail": {
                    "code": "INVALID_MAX_SERIES_POINTS",
                    "message": (
                        f"max_series_points must be >= 2 (got {max_series_points})."
                    ),
                }},
                "hint": "Correct max_series_points and retry — no backtest ran, "
                        "so this used no quota.",
            }

        run: dict[str, Any] = {"stats_keys": "ids", "response_detail": "full"}
        leg: dict[str, Any] = {"id": "strategy", "data_source": data_source}
        if strategy is not None:
            leg["strategy"] = strategy
        if signals is not None:
            leg["signals"] = signals
        if execution is not None:
            leg["execution"] = execution
        if data_inputs is not None:
            leg["data_inputs"] = data_inputs
        if include_leaf_series:
            leg["include_leaf_series"] = True
        legs = [leg]
        if benchmark is not None:
            benchmark_leg: dict[str, Any] = {
                "id": "benchmark", "data_source": benchmark, "benchmark": True,
            }
            if benchmark_exposure is not None:
                benchmark_leg["exposure"] = benchmark_exposure
            legs.append(benchmark_leg)
            run["reference"] = "benchmark"
        run.update(_run_overrides(run_start, run_end, run_signal_frequency))
        body = {"run": run, "legs": legs}
        try:
            resp = engine.backtest(body)
        except EngineError as exc:
            if exc.status in (400, 422):
                return _fixable(exc)
            raise
        legs_out = resp.get("legs") or []
        if not legs_out:
            raise ToolError("The engine returned an empty backtest response.")
        result = merge_leg_result(legs_out[0], resp.get("run") or {})
        if benchmark is None:
            # Single-leg alignment is trivial (nothing to align against) —
            # only surface it when a benchmark was actually requested.
            result.pop("alignment", None)
        series_points_kwargs = (
            {"series_points": max_series_points}
            if max_series_points is not None else {}
        )
        return shape_backtest_result(
            result,
            detail=response_detail,
            include=include,
            trades_limit=trades_limit,
            max_output_bytes=settings.max_output_bytes,
            **series_points_kwargs,
        )

    @mcp.tool()
    @engine_tool
    def get_latest_signal(
        data_source: dict[str, Any],
        strategy: dict[str, Any],
        execution: dict[str, Any] | None = None,
        data_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate the strategy on the most recent bar only — no P&L, no stats.

        Returns the latest signal (-1/0/1), which
        condition slots fired, and the bar timestamp. Use for "what would this
        strategy do right now" questions; use run_backtest for performance.
        """
        body: dict[str, Any] = {"data_source": data_source, "strategy": strategy}
        if execution is not None:
            body["execution"] = execution
        if data_inputs is not None:
            body["data_inputs"] = data_inputs
        try:
            resp = engine.latest_signal(body)
        except EngineError as exc:
            if exc.status in (400, 422):
                return _fixable(exc)
            raise
        return resp.get("result", resp)

    @mcp.tool()
    @engine_tool
    def compare_backtests(
        data_source: dict[str, Any],
        strategies: list[dict[str, Any]],
        include_benchmark: bool = False,
        benchmark: dict[str, Any] | None = None,
        benchmark_exposure: float | None = None,
        reference_label: str | None = None,
        run_start: str | None = None,
        run_end: str | None = None,
        run_signal_frequency: str | None = None,
        response_detail: Detail = "summary",
        trades_limit: int = 50,
    ) -> dict[str, Any]:
        """Run several strategies on the same data and compare side by side.

        One quota-counted call, but compute scales with the number of
        strategies. If the wall-clock compute budget is exceeded, the call
        fails with a tool error (504) instead of returning partial results —
        narrow the request (fewer strategies, shorter date range, coarser
        frequency) and retry.

        Args:
            data_source: Shared data source (same shape as run_backtest).
            strategies: List of {"label": str, "strategy": {...}|"signals":
                {...}, "execution": {...}?, "data_inputs": {...}?,
                "exposure": float?, "include_leaf_series": bool?} entries.
                Provide exactly one of "strategy" (condition-tree) or
                "signals" (precomputed, same shape as run_backtest's
                signals) per entry. Labels need not be unique or id-safe —
                they are echoed back verbatim in the result (give an entry a
                unique label if you intend to name it via reference_label).
                data_inputs is the same custom-time-series shape as
                run_backtest's data_inputs (name -> {dates, values}).
                "exposure" is only valid (≠ 1.0) on a benchmark leg — use
                benchmark_exposure for the auto-added benchmark leg instead of
                setting this on a strategy/signals entry. "include_leaf_series"
                adds a per-bar boolean 'series' array to that leg's
                signal_diagnostics leaves (visible at response_detail='full').
            include_benchmark: Add a buy-and-hold benchmark to the
                comparison, on the shared data_source unless benchmark
                overrides it.
            benchmark: Optional independent data source for the benchmark leg
                (same shape as data_source) — when given, the benchmark leg
                is added even if include_benchmark is left False.
            benchmark_exposure: Leverage knob for the benchmark leg (default
                1.0, must be in (0, 10]); only applies when a benchmark leg is
                added (include_benchmark=True or benchmark given).
            reference_label: Name any strategies[] entry's label (or the
                literal "benchmark") to use as the benchmark-relative baseline
                for every OTHER leg's "relative" block, instead of the
                engine's default (the sole benchmark leg, when exactly one is
                present). The named label must be unique among strategies[].
            run_start: Override every leg's data_source.start (symbol-fetched
                legs only; inline ohlcv legs are unaffected).
            run_end: Override every leg's data_source.end.
            run_signal_frequency: Override every leg's
                execution.signal_frequency.
            response_detail: Shaping level applied to each strategy's result.
            trades_limit: Max trades per strategy when detail is 'full'.

        Returns:
            {"strategies": [{"label", "result"}, ...], "equity_curves": {...},
            "alignment"?}, each result shaped at the requested detail. When a
            reference leg is resolved, non-reference entries also carry
            "relative" (beta, alpha, information ratio, etc.). A 400/422
            rejection returns {"accepted": false, "error": ...};
            capacity/timeout/permission failures raise a tool error.
        """
        body, label_by_id = _build_legs_body(
            data_source, strategies, include_benchmark,
            benchmark_data_source=benchmark,
            benchmark_exposure=benchmark_exposure,
            reference_label=reference_label,
            run_start=run_start, run_end=run_end,
            run_signal_frequency=run_signal_frequency,
        )
        try:
            resp = engine.backtest(body)
        except EngineError as exc:
            if exc.status in (400, 422):
                return _fixable(exc)
            raise
        return shape_compare_result(
            resp,
            label_by_id=label_by_id,
            detail=response_detail,
            trades_limit=trades_limit,
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def export_backtest(
        data_source: dict[str, Any],
        strategies: list[dict[str, Any]],
        include_benchmark: bool = False,
        benchmark: dict[str, Any] | None = None,
        benchmark_exposure: float | None = None,
        reference_label: str | None = None,
        run_start: str | None = None,
        run_end: str | None = None,
        run_signal_frequency: str | None = None,
    ) -> dict[str, Any]:
        """Export a multi-strategy comparison as an Excel workbook.

        Quota-counted; needs a key whose plan includes full-metrics export
        (a 403 means the configured key's plan does not — do not retry).
        Returns the workbook base64-encoded — decode and write it to a
        ``.xlsx`` file.

        Args:
            data_source: Shared data source (same shape as run_backtest).
            strategies: Same shape as compare_backtests' ``strategies``
                (including the optional per-entry ``data_inputs``, ``exposure``,
                ``include_leaf_series``, and either "strategy" or "signals"
                per entry).
            include_benchmark: Add a buy-and-hold benchmark to the export, on
                the shared data_source unless benchmark overrides it.
            benchmark: Optional independent data source for the benchmark leg
                (same shape as data_source) — when given, the benchmark leg
                is added even if include_benchmark is left False.
            benchmark_exposure: Leverage knob for the benchmark leg (default
                1.0, must be in (0, 10]); only applies when a benchmark leg is
                added (include_benchmark=True or benchmark given).
            reference_label: Name any strategies[] entry's label (or the
                literal "benchmark") to use as the benchmark-relative baseline
                for every OTHER leg, instead of the engine's default. Same
                semantics as compare_backtests' reference_label.
            run_start: Override every leg's data_source.start (symbol-fetched
                legs only; inline ohlcv legs are unaffected).
            run_end: Override every leg's data_source.end.
            run_signal_frequency: Override every leg's
                execution.signal_frequency.

        Returns:
            {"filename", "content_type", "size_bytes", "content_base64"}. A
            400/422 rejection returns {"accepted": false, "error": ...};
            capacity/timeout/permission failures raise a tool error. If the
            encoded workbook would exceed the output size limit, raises a
            tool error — narrow the request (shorter date range, fewer
            strategies, coarser frequency) and retry.
        """
        body, _ = _build_legs_body(
            data_source, strategies, include_benchmark,
            benchmark_data_source=benchmark,
            benchmark_exposure=benchmark_exposure,
            reference_label=reference_label,
            run_start=run_start, run_end=run_end,
            run_signal_frequency=run_signal_frequency,
        )
        try:
            raw = engine.export(body)
        except EngineError as exc:
            if exc.status in (400, 422):
                return _fixable(exc)
            raise
        content_b64 = base64.b64encode(raw).decode()
        if len(content_b64) > settings.max_output_bytes:
            raise ToolError(
                "The exported workbook is too large to return "
                f"({len(content_b64)} base64 bytes, limit "
                f"{settings.max_output_bytes}). Narrow the request — shorter "
                "date range, fewer strategies, or a coarser bar frequency — "
                "and retry."
            )
        return {
            "filename": "backtest360_export.xlsx",
            "content_type": _XLSX_CONTENT_TYPE,
            "size_bytes": len(raw),
            "content_base64": content_b64,
        }
