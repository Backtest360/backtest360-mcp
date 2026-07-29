"""Backtest360 MCP server — engine API as tools for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("backtest360-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"

from backtest360_mcp.engine_client import EngineClient, EngineError
from backtest360_mcp.settings import Settings

__all__ = ["EngineClient", "EngineError", "Settings", "__version__"]
