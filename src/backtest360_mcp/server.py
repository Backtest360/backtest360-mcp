"""Server assembly and stdio entry point."""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

from backtest360_mcp import __version__
from backtest360_mcp.engine_client import EngineClient
from backtest360_mcp.settings import Settings
from backtest360_mcp.tools import register_all

_INSTRUCTIONS = """\
Tools for the Backtest360 backtesting engine: discover indicators and \
reference catalogs, build and validate strategy documents, run historical \
backtests, compare strategies, and compute performance statistics. \
Recommended flow: engine_info once; get_me to learn what the configured key \
allows; get_catalog / list_indicators to ground every name and parameter in \
what actually exists, or start from a predesigned template via \
list_templates; validate_strategy until valid; then run_backtest \
(response_detail='summary' first, deeper only as needed). All numbers come \
from the engine — never estimate or extrapolate results. The configured API \
key's plan governs permissions, rate limits, and data access.\
"""


def create_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCPServer instance with every tool registered."""
    settings = settings or Settings.from_env()
    engine = EngineClient(
        engine_url=settings.engine_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
    )
    # Pin the version so serverInfo reports our package version, not the mcp
    # SDK's own (the default when unset).
    mcp = MCPServer("backtest360", instructions=_INSTRUCTIONS, version=__version__)
    register_all(mcp, engine, settings)
    return mcp


def main() -> None:
    """Console entry point — stdio transport."""
    settings = Settings.from_env()
    if not settings.api_key:
        print(
            "backtest360-mcp: BACKTEST360_API_KEY is not set. "
            "Export it (or wrap the command with your secrets manager) "
            "and restart.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    create_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
