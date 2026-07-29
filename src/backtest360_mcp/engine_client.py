"""Thin HTTP client for the Backtest360 engine API.

The server is a protocol adapter: requests go to the engine as-is, responses
come back as-is (tool-level shaping happens in :mod:`backtest360_mcp.shaping`).
This client adds only transport concerns — auth header, contract header,
timeouts, and uniform error mapping.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

# API contract version this server targets, sent via X-Client-Contract.
# The engine rejects the call with HTTP 409 when it no longer supports this
# contract, signalling that backtest360-mcp needs updating.
_CLIENT_CONTRACT = "1"


def _server_version() -> str:
    from backtest360_mcp import __version__

    return __version__


class EngineError(Exception):
    """Raised on any non-2xx response from the engine (or client-side failure).

    Attributes:
        status: HTTP status code (0 for client-side errors raised before any
            request was sent).
        code: Machine-readable engine error code when the response carried one
            (e.g. ``QUOTA_EXCEEDED``, ``COMPUTE_TIMEOUT``).
        body: Parsed response body (dict) or raw text when JSON parsing failed.
        request_id: ``X-Request-ID`` response header, when present. Joins the
            failure to the engine's logs.
        retry_after: Seconds to wait before retrying, from the ``Retry-After``
            header on capacity responses (429/503). ``None`` when absent.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        body: dict[str, Any] | str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body
        self.request_id = request_id
        self.retry_after = retry_after


class EngineClient:
    """Synchronous client for the engine endpoints the MCP server exposes.

    Args:
        engine_url: Engine base URL (no trailing slash).
        api_key: Key sent as ``X-API-Key`` on every request.
        timeout: Per-request timeout in seconds.
        transport: Optional httpx transport override (tests inject a
            ``httpx.MockTransport`` here).
    """

    def __init__(
        self,
        engine_url: str,
        api_key: str,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = engine_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    # -----------------------------------------------------------------------
    # Transport
    # -----------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "X-Client-Version": f"backtest360-mcp/{_server_version()}",
            "X-Client-Contract": _CLIENT_CONTRACT,
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> Any:
        """Send a request and return the parsed JSON response.

        Args:
            raw: When True, a 2xx response is returned as raw ``bytes``
                instead of being parsed as JSON (used for binary downloads).

        Raises:
            EngineError: On any non-2xx response, a non-JSON success body
                (unless ``raw``), or a payload that cannot be serialized.
        """
        url = f"{self._base_url}{path}"
        try:
            content = json.dumps(body, allow_nan=False) if body is not None else None
        except (ValueError, TypeError) as exc:
            raise EngineError(
                f"Payload is not JSON-serializable: {exc}. "
                "Check for NaN, Inf, or non-serializable values.",
                status=0,
                code="MCP_INVALID_PAYLOAD",
            ) from exc

        with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
            response = http.request(
                method, url, headers=self._headers(), params=params, content=content
            )

        if response.status_code >= 400:
            raise self._to_error(response)

        if raw:
            return response.content

        try:
            return response.json()
        except Exception:
            raise EngineError(
                f"Engine returned a non-JSON response (HTTP {response.status_code}).",
                status=response.status_code,
                code="MCP_MALFORMED_RESPONSE",
                body=response.text or None,
            ) from None

    @staticmethod
    def _to_error(response: httpx.Response) -> EngineError:
        """Map an error response to an EngineError, preserving the engine's
        structured detail (code, message) and operational headers."""
        try:
            resp_body: dict[str, Any] | str | None = response.json()
        except Exception:
            resp_body = response.text or None

        request_id = response.headers.get("x-request-id")
        retry_after_header = response.headers.get("retry-after")
        try:
            retry_after = float(retry_after_header) if retry_after_header else None
        except ValueError:
            retry_after = None

        code: str | None = None
        message = ""
        if isinstance(resp_body, dict):
            detail = resp_body.get("detail")
            if isinstance(detail, str):
                message = detail
            elif isinstance(detail, dict):
                message = detail.get("message", "") or response.text
                code = detail.get("code")
            elif isinstance(detail, list):
                # 422 request-validation errors arrive as a list of per-field dicts.
                parts = []
                for item in detail:
                    if isinstance(item, dict):
                        loc = " -> ".join(str(p) for p in item.get("loc", []))
                        msg = item.get("msg", "")
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    else:
                        parts.append(str(item))
                message = "; ".join(filter(None, parts)) or response.text
            else:
                message = (
                    resp_body.get("error") or resp_body.get("message") or response.text
                )
        else:
            message = str(resp_body) if resp_body else ""

        return EngineError(
            message or f"HTTP {response.status_code}",
            status=response.status_code,
            code=code,
            body=resp_body,
            request_id=request_id,
            retry_after=retry_after,
        )

    # -----------------------------------------------------------------------
    # Endpoints
    # -----------------------------------------------------------------------

    def version(self) -> dict[str, Any]:
        return self.request("GET", "/api/version")

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/health")

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/api/me")

    def strategies(
        self,
        collection: str | None = None,
        q: str | None = None,
        tags: str | None = None,
        detail: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if collection is not None:
            params["collection"] = collection
        if q is not None:
            params["q"] = q
        if tags is not None:
            params["tags"] = tags
        if detail is not None:
            params["detail"] = detail
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return self.request("GET", "/api/strategies", params=params or None)

    def indicators(self, descriptions: bool = True) -> Any:
        return self.request(
            "GET", "/api/indicators", params={"descriptions": descriptions}
        )

    def strategy_schema(self) -> dict[str, Any]:
        return self.request("GET", "/api/schemas/strategy")

    def catalog(self, path: str) -> Any:
        """Fetch one reference catalog by its API path (e.g. '/api/operators')."""
        return self.request("GET", path)

    def validate_strategy(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/validate-strategy.

        A failed validation arrives as HTTP 422 whose body *is* the validation
        result (``{"valid": false, "errors": [...]}``). That outcome is
        returned like a success — it is the agent's fix-and-retry input, not
        a transport failure.
        """
        try:
            return self.request("POST", "/api/validate-strategy", body=body)
        except EngineError as exc:
            if (
                exc.status == 422
                and isinstance(exc.body, dict)
                and "valid" in exc.body
            ):
                return exc.body
            raise

    def validate_indicator(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST /api/validate-indicator.

        Validates a single indicator entry (name/params/upstream) — lighter
        than embedding it in a full strategy. Like validate_strategy, a failed
        validation arrives as HTTP 422 whose body *is* the validation result
        (``{"valid": false, "errors": [...]}``); that outcome is returned like
        a success, not a transport failure.
        """
        try:
            return self.request("POST", "/api/validate-indicator", body=body)
        except EngineError as exc:
            if (
                exc.status == 422
                and isinstance(exc.body, dict)
                and "valid" in exc.body
            ):
                return exc.body
            raise

    def backtest(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/backtest", body=body)

    def export(self, body: dict[str, Any]) -> bytes:
        """POST /api/backtest/export — returns the raw xlsx workbook bytes."""
        return self.request("POST", "/api/backtest/export", body=body, raw=True)

    def latest_signal(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/latest-signal", body=body)

    def stats(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/stats", body=body)

    def ticker_search(self, query: str, asset_class: str | None, limit: int) -> Any:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if asset_class:
            params["asset_class"] = asset_class
        return self.request("GET", "/api/data/search", params=params)

    def tickers(self, asset_class: str | None) -> Any:
        params = {"asset_class": asset_class} if asset_class else None
        return self.request("GET", "/api/data/tickers", params=params)

    def data_range(self, symbol: str, frequency: str) -> Any:
        return self.request(
            "GET", "/api/data/range", params={"symbol": symbol, "frequency": frequency}
        )

    def ticker_info(self, symbol: str, frequency: str) -> Any:
        return self.request(
            "GET", "/api/data/info", params={"symbol": symbol, "frequency": frequency}
        )

    def quote(self, symbol: str, frequency: str) -> Any:
        return self.request(
            "GET", "/api/data/quote", params={"symbol": symbol, "frequency": frequency}
        )

    def price_history(
        self, symbol: str, start: str, frequency: str, end: str | None
    ) -> Any:
        params: dict[str, Any] = {
            "symbol": symbol,
            "start": start,
            "frequency": frequency,
        }
        if end:
            params["end"] = end
        return self.request("GET", "/api/data/history", params=params)

    def macro_catalog(self, category: str | None) -> Any:
        params = {"category": category} if category else None
        return self.request("GET", "/api/data/macro/series", params=params)

    def macro_observations(
        self, series: str, start: str | None, end: str | None
    ) -> Any:
        params: dict[str, Any] = {"series": series}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self.request("GET", "/api/data/macro", params=params)

    def data_samples(self) -> Any:
        return self.request("GET", "/api/data/samples")

    def data_sample(self, symbol: str) -> Any:
        return self.request("GET", "/api/data/sample", params={"symbol": symbol})

    def ticker_lookup(self, ticker: str) -> Any:
        return self.request("GET", f"/api/data/lookup/{quote(ticker, safe='')}")
