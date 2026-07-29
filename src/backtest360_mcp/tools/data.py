"""Market-data discovery tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from backtest360_mcp.engine_client import EngineClient
from backtest360_mcp.settings import Settings
from backtest360_mcp.shaping import (
    cap_output_size,
    cap_passthrough_list,
    shape_series_response,
)
from backtest360_mcp.tools import engine_tool

# The full ticker universe is ~140k rows / tens of MB — far past any useful
# discovery dump and large enough to break the stdio transport. Cap it hard and
# steer the caller to a filter or to search_tickers.
_LIST_TICKERS_MAX = 200

# search_tickers is normally bounded by its ``limit`` argument, but a caller can
# pass an arbitrarily large limit — cap the result defensively so it can never
# overflow the transport.
_SEARCH_TICKERS_MAX = 200


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    @mcp.tool()
    @engine_tool
    def search_tickers(
        query: str, asset_class: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Search available assets by ticker or name (relevance-ranked).

        Use to resolve a user's asset mention ("bitcoin",
        "S&P") to the exact ticker before requesting a server-side data fetch.
        asset_class filters to 'stocks', 'crypto', 'forex', or 'indices'.
        """
        return cap_passthrough_list(
            engine.ticker_search(query, asset_class, limit),
            list_key="results",
            max_items=_SEARCH_TICKERS_MAX,
            more=(
                "Result truncated by the MCP server. Refine the query or pass a "
                "smaller limit."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def list_tickers(asset_class: str | None = None) -> dict[str, Any]:
        """List available tickers, optionally filtered by asset class.

        The full universe is very large, so the MCP server
        caps the returned list and marks it ``truncated_by_mcp`` — pass
        asset_class to narrow it, or use search_tickers to resolve a specific
        asset by name.
        """
        return cap_passthrough_list(
            engine.tickers(asset_class),
            list_key="results",
            max_items=_LIST_TICKERS_MAX,
            more=(
                "Result truncated by the MCP server. Pass asset_class to filter, "
                "or use search_tickers to find a specific asset."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def get_data_range(symbol: str, frequency: str) -> dict[str, Any]:
        """Available date range and estimated bar count for a symbol/frequency.

        Available on paid plans. Call before a server-side fetch so the
        requested start/end stay inside what the provider can deliver and the
        bar count stays inside the key's per-run limit.
        """
        # A single small fixed-shape record (date range + bar count) — no list
        # to grow unbounded, so no size cap is needed here.
        return engine.data_range(symbol, frequency)

    @mcp.tool()
    @engine_tool
    def get_ticker_info(symbol: str, frequency: str = "daily") -> dict[str, Any]:
        """Identity and data coverage for one symbol, in a single call.

        Metadata only — no market data, so no paid plan is needed. Returns the
        asset's identity (name, asset class, exchange, currency, and whether it
        is still active) together with a coverage summary for the given
        frequency: the available date range and an estimated bar count. Use it
        to confirm a symbol resolves and that the history you need exists before
        requesting a quote or a price fetch. For the precise per-frequency range
        use get_data_range.
        """
        # A single small fixed-shape record (identity + coverage) — no list to
        # grow unbounded, so no size cap is needed here.
        return engine.ticker_info(symbol, frequency)

    @mcp.tool()
    @engine_tool
    def get_quote(symbol: str, frequency: str = "daily") -> dict[str, Any]:
        """Latest available price for a symbol.

        Requires a paid plan (managed market data). Returns the most recent
        *available* bar for the given frequency — the end-of-day close for
        daily, the last completed bar otherwise — as open/high/low/close/volume
        plus an ``as_of`` timestamp for that bar. This is a last-known price,
        not a live tick; read ``as_of`` to judge how stale it is.
        """
        # A single small fixed-shape record (one bar) — no size cap needed.
        return engine.quote(symbol, frequency)

    @mcp.tool()
    @engine_tool
    def get_price_history(
        symbol: str, start: str, frequency: str = "daily", end: str | None = None
    ) -> dict[str, Any]:
        """OHLCV price history for a symbol over a date range.

        Requires a paid plan (managed market data). ``start`` is required
        (``YYYY-MM-DD``); ``end`` defaults to today. Returns a summary (symbol,
        resolved date range, total bar count, price range, gap flags),
        market-hours detection, and the OHLCV arrays. A long history is
        downsampled by the MCP server to a bounded number of points — first and
        last bar always kept, every column thinned on the same dates — with
        ``downsampled_from_bars`` and ``points_returned`` recorded on the
        ``ohlcv`` block; the untouched ``summary.total_bars`` still reports the
        true bar count. The window is bounded by the plan's per-request bar cap
        — call get_data_range first to size a request.
        """
        return shape_series_response(
            engine.price_history(symbol, start, frequency, end),
            block_key="ohlcv",
            more=(
                "Price history downsampled by the MCP server to fit the output "
                "cap. Request a shorter date range or a coarser frequency for "
                "full resolution."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def list_macro_series(category: str | None = None) -> dict[str, Any]:
        """List the available macroeconomic series (the catalog).

        Free — no special plan. Returns the set of macro series you can fetch
        with get_macro_series, each with its stable ``id`` (the value
        get_macro_series takes), title, category, native reporting frequency,
        and units, plus the list of categories. Optionally filter to one
        ``category`` (e.g. rates, yield_curve, inflation, employment, recession,
        growth). Call this first to find the ``id`` for the series you want.
        """
        return cap_output_size(
            engine.macro_catalog(category),
            more=(
                "The macro-series catalog was too large to return in full and "
                "was thinned by the MCP server."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def get_macro_series(
        series: str, start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        """Observations for one macroeconomic series over an optional date range.

        Free — no special plan. ``series`` is an ``id`` from list_macro_series
        (e.g. treasury_10y, cpi, unemployment_rate); arbitrary external ids are
        not accepted. ``start``/``end`` are ``YYYY-MM-DD``, inclusive, both
        optional (full history when omitted). Returns the value series at its
        native reporting frequency, with the series descriptor and an ``as_of``
        date. A long history is downsampled by the MCP server to a bounded
        number of points (first and last kept), marked with
        ``downsampled_from_bars`` and ``points_returned`` on the
        ``observations`` block.

        Note: values are the latest revised figures stamped by reference period,
        not point-in-time as-first-reported data — do not treat them as the
        values that were known at a past date.
        """
        return shape_series_response(
            engine.macro_observations(series, start, end),
            block_key="observations",
            more=(
                "Macro observations downsampled by the MCP server to fit the "
                "output cap. Request a shorter date range for full resolution."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def list_data_samples() -> dict[str, Any]:
        """List the bundled sample symbols usable without real market data.

        Free — no plan required (the engine touches no provider for this).
        Returns the small fixed set of symbols that have a built-in sample
        OHLCV dataset. Pass one of these to get_data_sample to fetch it — a
        way to try a backtest or the client examples before supplying your
        own data or paying for managed market data.
        """
        # A single small fixed-shape list (the built-in sample catalog, not the
        # ticker universe) — no size cap needed.
        return engine.data_samples()

    @mcp.tool()
    @engine_tool
    def get_data_sample(symbol: str) -> dict[str, Any]:
        """Fetch a bundled sample OHLCV dataset by symbol.

        Free — no plan required (no provider call; this is static data
        shipped with the engine, one fixed historical year of daily bars per
        symbol). symbol must be one of the values from list_data_samples. Use
        this to try a backtest or the client examples without supplying your
        own data source or requiring a paid data plan. Returns the same
        summary/market_hours/ohlcv shape as get_price_history, with
        summary.source always "sample" — pass the ohlcv block straight
        through to run_backtest/compare_backtests as data_source.
        """
        return shape_series_response(
            engine.data_sample(symbol),
            block_key="ohlcv",
            more=(
                "Sample dataset downsampled by the MCP server to fit the "
                "output cap."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def get_ticker_lookup(ticker: str) -> dict[str, Any]:
        """Exact-ticker identity lookup: name, asset class, exchange, currency.

        Requires a paid plan (provider metadata). Unlike get_ticker_info
        (which also folds in a data-coverage summary from get_data_range in
        the same call), this returns identity fields only, for an exact
        ticker match — no coverage/date-range data. Use get_ticker_info
        instead when you also need the available date range or bar count.
        """
        # A single small fixed-shape record (identity only) — no size cap
        # needed.
        return engine.ticker_lookup(ticker)
