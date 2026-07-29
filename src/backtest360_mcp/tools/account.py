"""Key introspection — what the configured API key can do."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from backtest360_mcp.engine_client import EngineClient
from backtest360_mcp.settings import Settings
from backtest360_mcp.tools import engine_tool


def register(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    @mcp.tool()
    @engine_tool
    def get_me() -> dict[str, Any]:
        """The configured API key's permissions, limits, and current usage.

        Cheap. Call early in a session — before planning work — to learn what
        this key can do instead of discovering limits through failed calls.

        Returns:
            ``scopes``: the permission scopes the key carries. ``limits``:
            requests per minute and per day, max concurrent requests, and the
            per-run bar cap (null when uncapped). ``usage``: current
            consumption against those limits, with reset countdowns in
            seconds. ``capabilities``: feature flags such as server-side data
            fetch and the full metric set. A small fixed-shape record,
            returned as the engine sent it.
        """
        # A single small fixed-shape record — no list to grow unbounded, so
        # no size cap is needed here.
        return engine.me()
