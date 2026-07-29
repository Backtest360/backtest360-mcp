"""Strategy validation — the fix-and-retry backstop."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from backtest360_mcp.engine_client import EngineClient
from backtest360_mcp.settings import Settings
from backtest360_mcp.tools import engine_tool


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    @mcp.tool()
    @engine_tool
    def validate_strategy(
        strategy: dict[str, Any],
        injected_indicators: list[str] | None = None,
    ) -> dict[str, Any]:
        """Validate a strategy document without running a backtest.

        A cheap quota separate from backtest runs,
        so validate freely and ALWAYS before run_backtest.

        Args:
            strategy: The strategy document — name, indicators[], and
                condition_tree (see get_strategy_schema for the exact shape).
            injected_indicators: Names of custom time-series columns the
                caller will supply via data_inputs at run time, so conditions
                referencing them validate.

        Returns:
            On success: {"valid": true, "warmup_bars": ..., referenced
            indicators/columns}. On failure: {"valid": false, "errors": [...]}
            where each error carries a machine code, the location in the
            document, a message, and context (e.g. the list of valid column
            names). A failed validation is a NORMAL result, not an error —
            read the errors, fix the document, and validate again before
            running.
        """
        body: dict[str, Any] = {"strategy": strategy}
        if injected_indicators:
            body["injected_indicators"] = injected_indicators
        return engine.validate_strategy(body)

    @mcp.tool()
    @engine_tool
    def validate_indicator(
        name: str,
        params: dict[str, Any] | None = None,
        upstream: list[str] | None = None,
    ) -> dict[str, Any]:
        """Validate a single indicator entry — name, params, upstream refs.

        A cheap quota separate from backtest runs and lighter than
        validate_strategy: use this while composing or editing one indicator,
        before it goes into a strategy's indicators[] list.

        Args:
            name: Indicator id or name from list_indicators (e.g. 'rsi').
            params: The indicator's parameters (see list_indicators(name=...)
                for its params_schema). Omit for an indicator with no params.
            upstream: Ref ids of upstream indicators this one is computed
                from, for indicators that declare an upstream dependency.

        Returns:
            On success: {"valid": true, "warmup_bars": ...}. On failure:
            {"valid": false, "errors": [...]} where each error carries a
            machine code (e.g. UNKNOWN_INDICATOR, INVALID_INDICATOR_PARAMS),
            the location in the entry, a message, and context. A failed
            validation is a NORMAL result, not an error — read the errors,
            fix the entry, and validate again.
        """
        body: dict[str, Any] = {"name": name}
        if params:
            body["params"] = params
        if upstream:
            body["upstream"] = upstream
        return engine.validate_indicator(body)
