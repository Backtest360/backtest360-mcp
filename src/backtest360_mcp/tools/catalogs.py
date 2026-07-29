"""Reference/catalog tools — cheap discovery calls."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from backtest360_mcp.engine_client import EngineClient, EngineError
from backtest360_mcp.settings import Settings
from backtest360_mcp.shaping import cap_output_size, cap_passthrough_list
from backtest360_mcp.tools import engine_tool

CatalogName = Literal[
    "operators",
    "execution-modes",
    "stop-types",
    "sizing-methods",
    "bar-frequencies",
    "sections",
    "sampling-modes",
]

_CATALOG_PATHS: dict[str, str] = {
    "operators": "/api/operators",
    "execution-modes": "/api/execution-modes",
    "stop-types": "/api/stop-types",
    "sizing-methods": "/api/sizing-methods",
    "bar-frequencies": "/api/bar-frequencies",
    "sections": "/api/sections",
    "sampling-modes": "/api/sampling-modes",
}

_COMPACT_FIELDS = ("id", "name", "category", "kind", "value_dtype")

# Compact discovery view of a strategy template: enough to choose one
# (description included — it is the selection signal), none of the heavy
# definition blocks (condition_tree, indicators, parameter metadata).
_TEMPLATE_COMPACT_FIELDS = ("id", "origin", "name", "description")


def _indicator_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("indicators", [])
    return []


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    @mcp.tool()
    @engine_tool
    def engine_info() -> dict[str, Any]:
        """Engine version, API contract number, and health.

        Free (not quota-counted). Call once at the start of a session
        to confirm the engine is reachable and which contract it serves.
        """
        info = engine.version()
        try:
            info["health"] = engine.health().get("status")
        except Exception:
            info["health"] = "unreachable"
        return info

    @mcp.tool()
    @engine_tool
    def get_catalog(catalog: CatalogName) -> dict[str, Any]:
        """Fetch one engine reference catalog.

        Catalogs (cheap, cacheable per session):
        - 'operators' — comparison operators for condition expressions
        - 'execution-modes' — entry/exit anchors and fill algorithms, with the
          validity matrix by market type
        - 'stop-types' — stop-loss types, re-entry modes, and their parameters
        - 'sizing-methods' — position-sizing methods and their parameters
        - 'bar-frequencies' — supported bar frequencies and the signal x
          execution validity matrix (which combinations are allowed)
        - 'sections' — the full metric catalog: every statistic's stable id,
          display label, section, and description
        - 'sampling-modes' — Monte-Carlo resampling modes, each with its
          status and parameters. Reference only: this MCP has no tool to run
          a Monte Carlo simulation, so treat this catalog as informational,
          not something to build a workflow on.

        Fetch the relevant catalog BEFORE building a strategy or config; build
        only from values it lists — never guess parameter names or frequencies.
        """
        return cap_output_size(
            engine.catalog(_CATALOG_PATHS[catalog]),
            more=(
                f"The {catalog!r} catalog was too large to return in full and "
                "was thinned by the MCP server."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    # structured_output=False: the return is a single JSON object — the catalog,
    # or one indicator's entry when name= is given — emitted as one content
    # block. MCPServer's auto-generated schema for `Any` returns differs across
    # Python versions, so content-only keeps the result identical everywhere;
    # and a dict (rather than a bare list) serializes as ONE block instead of
    # one-per-indicator, which would otherwise overflow a client on discovery.
    @mcp.tool(structured_output=False)
    @engine_tool
    def list_indicators(
        name: str | None = None, compact: bool = True
    ) -> Any:
        """List indicators, or fetch one indicator's full schema.

        Cheap, cacheable per session.

        With no arguments: a compact catalog — ``{"indicators": [...],
        "count": N}`` — where each entry carries id, name, category, kind,
        and value_dtype (no description, to keep the discovery scan small). Use
        it to discover what exists. Pass name='rsi' (id or name,
        case-insensitive) to get that single indicator's complete entry
        including its description and params_schema — do this before adding an
        indicator to a strategy so its parameters are exactly right.
        Pass compact=False for full entries for everything (large; the MCP
        server may cap it and set ``truncated_by_mcp`` — prefer compact or
        name=).

        Wire optimization: the compact discovery path asks the engine to omit
        per-entry descriptions (``descriptions=false``) since they are stripped
        locally anyway; the name= and compact=False paths request them. This is
        a pure saving — if the engine ignores the param it returns full entries
        and the local compact strip still yields a lean result.
        """
        # Descriptions are only ever emitted when fetching one named entry or
        # the full (compact=False) catalog; the default compact scan drops them.
        raw = engine.indicators(descriptions=name is not None or not compact)
        entries = _indicator_list(raw)
        if name is not None:
            wanted = name.lower()
            for entry in entries:
                if (
                    str(entry.get("id", "")).lower() == wanted
                    or str(entry.get("name", "")).lower() == wanted
                ):
                    return entry
            raise ValueError(
                f"No indicator named {name!r}. Call list_indicators() without "
                "arguments to see what is available."
            )
        if compact:
            entries = [
                {k: e[k] for k in _COMPACT_FIELDS if k in e} for e in entries
            ]
        payload: dict[str, Any] = {"indicators": entries, "count": len(entries)}
        if isinstance(raw, dict) and raw.get("version") is not None:
            payload["version"] = raw["version"]  # catalog version, for caching
        # One JSON object, never a bare list. max_items=len keeps the catalog
        # complete; only the byte ceiling can thin it (the large compact=False
        # path), and only then is it marked truncated_by_mcp.
        return cap_passthrough_list(
            payload,
            list_key="indicators",
            max_items=len(entries),
            more=(
                "Result truncated by the MCP server. Call list_indicators() with "
                "compact=true for the smaller catalog, or name=<id> for one "
                "indicator's full schema."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    # structured_output=False for the same reason as list_indicators: the
    # return is one JSON object (the catalog, or a single template's entry
    # when name= is given) emitted as exactly one content block, shaped
    # identically on every Python version.
    @mcp.tool(structured_output=False)
    @engine_tool
    def list_templates(
        name: str | None = None,
        compact: bool = True,
        collection: str = "curated",
        q: str | None = None,
        tags: str | None = None,
        limit: int = 50,
    ) -> Any:
        """List predesigned strategy templates, or fetch one in full.

        Cheap, cacheable per session. The engine returns the templates
        available to the calling key.

        By default this returns the curated set of built-in templates. A
        larger template library is available by passing collection='all'
        (or another named collection), optionally narrowed with q (a
        substring match against name and description) and tags
        (comma-separated; a template must carry every listed tag). Results
        are paged — limit caps how many templates come back in one call
        (default 50); when more remain, the response carries a next_offset
        to continue from.

        With no name: a compact catalog — ``{"templates": [...], "count":
        N, "total": N, "next_offset": N | null}`` — where each entry
        carries id, origin, name, and description. Use it to discover what
        exists. Pass name='sma-cross' (id or name, case-insensitive) to get
        that single template's complete entry: its strategy logic
        (``condition_tree`` + ``indicators``, the same shape
        validate_strategy and run_backtest accept) plus parameter metadata
        — ``defaults`` (starting parameter values), ``requires``, and
        ``locked_params`` (parameters that must keep their template
        values). name= looks across every collection, not just the curated
        default. Pass compact=False for complete entries for everything the
        current filters match (large; the MCP server may cap it and set
        ``truncated_by_mcp`` — prefer compact, narrower filters, or name=).
        """
        if name is not None:
            raw = engine.strategies(collection="all")
            entries = raw.get("strategies") if isinstance(raw, dict) else None
            if not isinstance(entries, list):
                entries = []
            wanted = name.lower()
            for entry in entries:
                if (
                    str(entry.get("id", "")).lower() == wanted
                    or str(entry.get("name", "")).lower() == wanted
                ):
                    return entry
            raise ValueError(
                f"No template named {name!r}. Call list_templates() without "
                "arguments to see what is available."
            )

        raw = engine.strategies(collection=collection, q=q, tags=tags, limit=limit)
        entries = raw.get("strategies") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            entries = []
        if compact:
            entries = [
                {k: e[k] for k in _TEMPLATE_COMPACT_FIELDS if k in e}
                for e in entries
            ]
        payload: dict[str, Any] = {"templates": entries, "count": len(entries)}
        if isinstance(raw, dict):
            if raw.get("total") is not None:
                payload["total"] = raw["total"]
            if raw.get("next_offset") is not None:
                payload["next_offset"] = raw["next_offset"]
        # max_items=len keeps the catalog complete; only the byte ceiling can
        # thin it (the large compact=False path), marked truncated_by_mcp.
        return cap_passthrough_list(
            payload,
            list_key="templates",
            max_items=len(entries),
            more=(
                "Result truncated by the MCP server. Call list_templates() "
                "with compact=true for the smaller catalog, narrower "
                "q/tags filters, or name=<id> for one template's full "
                "definition."
            ),
            max_output_bytes=settings.max_output_bytes,
        )

    @mcp.tool()
    @engine_tool
    def get_strategy_schema() -> dict[str, Any]:
        """JSON Schema for the strategy document (condition_tree + indicators).

        Fetch this before composing a strategy by hand; the
        validate_strategy tool checks against the same rules.
        """
        return engine.strategy_schema()

    # Cheap static catalogs doubled as MCP resources, for clients that support
    # resource attachment. Never the only path — the tools above cover all of it.
    @mcp.resource("backtest360://catalog/{catalog}")
    def catalog_resource(catalog: str) -> str:
        path = _CATALOG_PATHS.get(catalog)
        if path is None:
            return json.dumps(
                {"error": f"unknown catalog {catalog!r}", "available": sorted(_CATALOG_PATHS)}
            )
        try:
            return json.dumps(engine.catalog(path), default=str)
        except EngineError as exc:
            return json.dumps({"error": str(exc), "status": exc.status})

    @mcp.resource("backtest360://schema/strategy")
    def strategy_schema_resource() -> str:
        try:
            return json.dumps(engine.strategy_schema(), default=str)
        except EngineError as exc:
            return json.dumps({"error": str(exc), "status": exc.status})
