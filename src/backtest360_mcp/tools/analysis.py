"""Standalone statistics computation."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from backtest360_mcp.engine_client import EngineClient, EngineError
from backtest360_mcp.settings import Settings
from backtest360_mcp.tools import engine_tool, fixable_result


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    @mcp.tool()
    @engine_tool
    def compute_stats(
        returns: dict[str, Any],
        trading_days_per_year: int,
        benchmark_returns: dict[str, Any] | None = None,
        trades: list[dict[str, Any]] | None = None,
        risk_free_rate: float = 0.0,
    ) -> dict[str, Any]:
        """Compute the engine's performance metrics from a returns series.

        Use when the returns came from somewhere
        other than run_backtest (an external system, a portfolio) — backtest
        results already include these statistics.

        Args:
            returns: Per-bar log returns as {"dates": [...], "values": [...]}
                parallel arrays (ISO-8601 dates).
            trading_days_per_year: Required annualization factor — 252 for a
                daily equities calendar, 365 for 24/7 crypto. Must match the bar
                calendar of the returns series; a wrong value silently
                mis-annualizes Sharpe, volatility, and CAGR.
            benchmark_returns: Optional benchmark series, same shape — adds
                alpha/beta/capture metrics.
            trades: Optional trade records (entry_date, exit_date, direction,
                return_net, ...) — adds trade-level metrics.
            risk_free_rate: Annual risk-free rate as a decimal.

        Returns:
            {"stats": {...}} — the metric set the API key's plan allows.
            See get_catalog('sections') for every metric's id and description.
        """
        body: dict[str, Any] = {
            "returns": returns,
            "config": {
                "risk_free_rate": risk_free_rate,
                "trading_days_per_year": trading_days_per_year,
            },
            "stats_keys": "ids",
        }
        if benchmark_returns is not None:
            body["benchmark_returns"] = benchmark_returns
        if trades is not None:
            body["trades"] = trades
        try:
            resp = engine.stats(body)
        except EngineError as exc:
            if exc.status in (400, 422):
                return fixable_result(
                    exc,
                    "Fix the named field(s) and retry — dates and values "
                    "must be equal-length parallel arrays.",
                )
            raise
        return {"stats": resp.get("stats", resp)}
