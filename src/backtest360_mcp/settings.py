"""Runtime configuration, resolved from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_ENGINE_URL = "https://api.backtest360.com"
_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_BYTES = 100_000


@dataclass(frozen=True)
class Settings:
    """Server configuration.

    Attributes:
        engine_url: Base URL of the Backtest360 engine API.
        api_key: Engine API key sent as ``X-API-Key`` on every request.
            One key per server process; the key's permissions and quotas govern
            what the connected agent can do.
        timeout: Per-request HTTP timeout in seconds (backtests can be slow).
        max_output_bytes: Hard cap on the serialized size of any single tool
            result. Oversized results are reduced and marked
            ``truncated_by_mcp`` — never silently cut.
    """

    engine_url: str = _DEFAULT_ENGINE_URL
    api_key: str = ""
    timeout: float = _DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from environment variables.

        Reads ``BACKTEST360_ENGINE_URL``, ``BACKTEST360_API_KEY``,
        ``BACKTEST360_MCP_TIMEOUT``, and ``BACKTEST360_MCP_MAX_OUTPUT_BYTES``.
        """
        return cls(
            engine_url=(
                os.environ.get("BACKTEST360_ENGINE_URL") or _DEFAULT_ENGINE_URL
            ).rstrip("/"),
            api_key=os.environ.get("BACKTEST360_API_KEY", ""),
            timeout=float(
                os.environ.get("BACKTEST360_MCP_TIMEOUT", _DEFAULT_TIMEOUT_SECONDS)
            ),
            max_output_bytes=int(
                os.environ.get(
                    "BACKTEST360_MCP_MAX_OUTPUT_BYTES", _DEFAULT_MAX_OUTPUT_BYTES
                )
            ),
        )
