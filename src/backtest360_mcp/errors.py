"""Engine error → agent-actionable tool error mapping.

Principle: errors the agent can fix by changing its input are tool *results*
(handled in engine_client / the tools); errors it cannot fix that way are
tool *errors* with explicit guidance on what to do instead.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from backtest360_mcp.engine_client import EngineError

_GUIDANCE_BY_STATUS = {
    401: "The API key was rejected. Check BACKTEST360_API_KEY is set to a valid key.",
    403: (
        "The API key lacks a required capability for this call. "
        "This is a plan/permission limit — do not retry; tell the user "
        "which capability is missing."
    ),
    404: "Endpoint not available on this engine.",
    409: (
        "Client contract mismatch — this backtest360-mcp release targets an API "
        "contract the engine no longer supports. Upgrade backtest360-mcp."
    ),
    413: (
        "Request too large. Reduce the payload: fewer bars (shorter date range "
        "or coarser frequency), fewer strategies in a comparison, or shorter "
        "series."
    ),
    429: "Rate limited.",
    500: (
        "Engine-side error — an unexpected failure your input cannot fix. Do "
        "NOT retry the same request; report it with the request id if shown."
    ),
    501: "The engine does not implement this capability. Do not retry.",
    502: "Upstream/provider failure. Transient — retry with backoff.",
    503: "Engine is at capacity. Transient — retry with backoff.",
    504: (
        "Compute time limit exceeded. Do NOT retry the same request — reduce "
        "the date range, use a coarser bar frequency, or simplify the strategy."
    ),
}


def to_tool_error(exc: EngineError) -> ToolError:
    """Convert an EngineError into a ToolError whose message tells the agent
    what happened and what to do about it."""
    parts: list[str] = []
    if exc.status:
        label = exc.code or f"HTTP_{exc.status}"
    else:
        label = exc.code or "ERROR"
    parts.append(f"[{label}] {exc}")

    guidance = _GUIDANCE_BY_STATUS.get(exc.status)
    if guidance:
        parts.append(guidance)

    if exc.retry_after is not None:
        parts.append(f"Retry after {exc.retry_after:g} seconds.")
    elif exc.status == 429:
        parts.append("Wait before retrying.")

    if exc.request_id:
        parts.append(f"(request id: {exc.request_id})")

    return ToolError(" ".join(parts))
