"""Tool registration — one module per engine area."""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backtest360_mcp.engine_client import EngineClient, EngineError
from backtest360_mcp.errors import to_tool_error
from backtest360_mcp.settings import Settings

F = TypeVar("F", bound=Callable[..., Any])


def engine_tool(fn: F) -> F:
    """Wrap a tool body: engine failures become agent-actionable ToolErrors,
    bad tool arguments (ValueError) are reported verbatim."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EngineError as exc:
            raise to_tool_error(exc) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def fixable_result(exc: EngineError, hint: str) -> dict[str, Any]:
    """Render a request-validation rejection (400/422) as a normal result the
    agent can read and act on, instead of a tool error."""
    return {
        "accepted": False,
        "status": exc.status,
        "error": exc.body if isinstance(exc.body, dict) else {"message": str(exc)},
        "hint": hint,
    }


def register_all(mcp: MCPServer, engine: EngineClient, settings: Settings) -> None:
    """Register every tool, resource, and prompt on the server instance."""
    from backtest360_mcp import prompts
    from backtest360_mcp.tools import (
        account,
        analysis,
        backtest,
        catalogs,
        data,
        strategy,
    )

    account.register(mcp, engine, settings)
    catalogs.register(mcp, engine, settings)
    strategy.register(mcp, engine, settings)
    backtest.register(mcp, engine, settings)
    analysis.register(mcp, engine, settings)
    data.register(mcp, engine, settings)
    # Prompts are static workflow scaffolding — no engine or settings needed.
    prompts.register(mcp)
