"""Shared fixtures — a canned mock engine served over httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from backtest360_mcp.engine_client import EngineClient
from backtest360_mcp.settings import Settings

N_BARS = 1_000
N_TRADES = 80


def _dates(n: int) -> list[str]:
    start = datetime(2020, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def make_backtest_result(n_bars: int = N_BARS, n_trades: int = N_TRADES) -> dict[str, Any]:
    """A synthetic but shape-faithful per-leg ``result`` block (the value of
    ``resp["legs"][i]["result"]`` in a v0.12 ``/api/backtest`` response).

    ``created_at``, ``symbol``, and top-level ``warnings`` no longer live
    here in the real engine — they live on the envelope's ``run`` block, the
    leg wrapper, and ``stats["_warnings"]`` respectively (see MockEngine).
    """
    dates = _dates(n_bars)
    # Keyed by stable snake_case metric id (stats_keys="ids"), not display
    # label — the id vocabulary published in the metrics catalog on
    # GET /api/sections.
    stats = {
        "total_return": 0.42, "cagr": 0.11, "ytd": 0.03, "return_1y": 0.09,
        "vol_ann": 0.18, "sharpe": 1.2, "sortino": 1.6,
        "probabilistic_sharpe": 0.88, "calmar": 0.8,
        "max_drawdown": -0.21, "max_dd_length": 87, "worst_day": -0.06,
        "var_5pc": -0.025, "time_in_market_pct": 0.64, "total_trades": n_trades,
        "trade_win_rate": 0.55, "win_rate": 0.52, "avg_trade_pnl": 0.004,
        "avg_holding_days": 9.5, "best_trade": 0.12, "profit_factor": 1.4,
        "kurtosis": 5.1,
        # Extra metrics beyond the headline set, not present in METRIC_IDS
        # (fixture-only paid-extra stand-ins) — no id to convert to.
        "Omega": 1.3, "Skew": -0.4, "Ulcer Index": 0.07,
        "_warnings": [
            {"code": "ZERO_COSTS_CONFIGURED", "severity": "info",
             "message": "No costs configured.", "context": {}},
        ],
    }
    return {
        "stats": stats,
        "series": {
            "dates": dates,
            "returns": [0.001] * n_bars,
            "strategy_equity": [1.0 + i * 0.0004 for i in range(n_bars)],
            "benchmark_equity": [1.0 + i * 0.0003 for i in range(n_bars)],
            "drawdown": [0.0] * n_bars,
            "signals": [1] * n_bars,
            "positions": [1.0] * n_bars,
        },
        "monthly_returns": [{"period": "2020-01", "return": 0.01}],
        "yearly_returns": [{"period": "2020", "return": 0.12}],
        "rolling_statistics": {
            "window_bars": 252,
            "dates": dates,
            "return_12m": [0.1] * n_bars,
            "vol_ann": [0.18] * n_bars,
            "sharpe_ann": [1.1] * n_bars,
        },
        "results_df": {"state": ["HOLD"] * n_bars},
        "trades": [
            {
                "entry_date": dates[i], "exit_date": dates[i + 1], "direction": 1,
                "entry_price": 100.0, "exit_price": 101.0, "return_net": 0.01,
                "holding_bars": 1, "exit_reason": "exit_signal",
            }
            for i in range(n_trades)
        ],
        "signal_diagnostics": {"long_entry_fired": [False] * n_bars},
        "data_quality": {"bad_prices": 0, "missing_bars": 0, "quality_warnings": []},
        "markers": {"warmup_bars": 14, "first_trade_index": 14},
    }


VALIDATE_OK = {
    "valid": True,
    "warmup_bars": 14,
    "referenced_indicators": ["rsi_14"],
    "warnings": [],
}

VALIDATE_FAIL = {
    "valid": False,
    "errors": [
        {
            "code": "UNKNOWN_COLUMN_REF",
            "location": "/condition_tree/long_entry/",
            "message": "Unknown column reference(s): ['rsi_15']",
            "context": {"unknown": ["rsi_15"], "available": ["close", "rsi_14"]},
        }
    ],
}

VALIDATE_INDICATOR_OK = {"valid": True, "warmup_bars": 14}

VALIDATE_INDICATOR_FAIL = {
    "valid": False,
    "errors": [
        {
            "code": "UNKNOWN_INDICATOR",
            "location": "/name",
            "message": "No indicator named 'not-a-real-indicator'.",
            "context": {},
        }
    ],
}

INDICATORS = {
    "version": "abc123",
    "indicators": [
        {"id": "rsi", "name": "RSI", "description": "Relative Strength Index",
         "category": "Momentum", "kind": "technical", "value_dtype": "numeric",
         "params_schema": {"properties": {"period": {"type": "integer"}}}},
        {"id": "sma", "name": "SMA", "description": "Simple Moving Average",
         "category": "Overlap", "kind": "technical", "value_dtype": "numeric",
         "params_schema": {"properties": {"period": {"type": "integer"}}}},
    ],
}

ME = {
    "scopes": ["backtest.run", "meta.read", "strategy.validate"],
    "limits": {"rpm": 30, "rpd": 500, "max_concurrent": 2,
               "max_bars_per_run": 100_000},
    "usage": {
        "minute": {"used": 3, "remaining": 27, "resets_in_seconds": 41},
        "day": {"used": 120, "remaining": 380, "resets_in_seconds": 52_000},
        "concurrent": {"used": 1, "remaining": 1},
    },
    "capabilities": {"server_side_fetch": False, "full_metrics": True},
}

TEMPLATES = {
    "strategies": [
        {"id": "sma-cross", "origin": "system", "name": "SMA Cross",
         "description": "Fast/slow moving-average crossover.",
         "condition_tree": {
             "long_entry": {"op": "leaf", "expr": "sma_fast > sma_slow"},
             "long_exit": {"op": "leaf", "expr": "sma_fast < sma_slow"},
             "short_entry": None, "short_exit": None,
         },
         "indicators": [
             {"ref": "sma_fast", "name": "SMA", "kind": "technical",
              "params": {"period": 10}, "upstream": []},
             {"ref": "sma_slow", "name": "SMA", "kind": "technical",
              "params": {"period": 50}, "upstream": []},
         ],
         "requires": {}, "defaults": {"sma_fast.period": 10},
         "locked_params": []},
        {"id": "rsi-reversion", "origin": "system", "name": "RSI Reversion",
         "description": "Buy oversold, exit on recovery.",
         "condition_tree": {
             "long_entry": {"op": "leaf", "expr": "rsi_14 < 30"},
             "long_exit": {"op": "leaf", "expr": "rsi_14 > 50"},
             "short_entry": None, "short_exit": None,
         },
         "indicators": [
             {"ref": "rsi_14", "name": "RSI", "kind": "technical",
              "params": {"period": 14}, "upstream": []},
         ],
         "requires": {}, "defaults": {}, "locked_params": []},
    ],
}

CATALOGS = {
    "/api/operators": {"operators": [{"id": "gt", "label": ">"}]},
    "/api/execution-modes": {"modes": ["close_exact"]},
    "/api/stop-types": {"stop_types": ["atr"]},
    "/api/sizing-methods": {"methods": ["static"]},
    "/api/bar-frequencies": {"frequencies": ["daily", "hourly"]},
    "/api/sections": {"sections": [], "metrics": [{"id": "sharpe", "label": "Sharpe"}]},
    "/api/sampling-modes": {
        "modes": [
            {"id": "iid_bootstrap", "name": "IID Bootstrap", "status": "active", "parameters": []}
        ]
    },
}


class MockEngine:
    """Routing table + request capture for the mock engine."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.backtest_result = make_backtest_result()
        # Per-path overrides: path -> (status, json_body, headers)
        self.overrides: dict[str, tuple[int, Any, dict[str, str]]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            status, body, headers = self.overrides[path]
            return httpx.Response(status, json=body, headers=headers)
        if path == "/api/version":
            return httpx.Response(200, json={
                "version": "0.12.0", "engine": "backtest360", "api_contract": "4",
                "expected_client_contract": "1",
            })
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/me":
            return httpx.Response(200, json=ME)
        if path == "/api/strategies":
            return httpx.Response(200, json=TEMPLATES)
        if path == "/api/indicators":
            return httpx.Response(200, json=INDICATORS)
        if path == "/api/schemas/strategy":
            return httpx.Response(200, json={"$schema": "https://json-schema.org/draft/2020-12/schema"})
        if path in CATALOGS:
            return httpx.Response(200, json=CATALOGS[path])
        if path == "/api/validate-strategy":
            body = json.loads(request.content)
            name = (body.get("strategy") or {}).get("name", "")
            if name == "bad":
                return httpx.Response(422, json=VALIDATE_FAIL)
            return httpx.Response(200, json=VALIDATE_OK)
        if path == "/api/validate-indicator":
            body = json.loads(request.content)
            if body.get("name") == "not-a-real-indicator":
                return httpx.Response(422, json=VALIDATE_INDICATOR_FAIL)
            return httpx.Response(200, json=VALIDATE_INDICATOR_OK)
        if path == "/api/backtest":
            body = json.loads(request.content)
            legs_req = body.get("legs") or []
            reference = "benchmark" if any(leg.get("benchmark") for leg in legs_req) else None
            dates = self.backtest_result["series"]["dates"]
            relative = {
                "beta": 0.9, "alpha": 0.02, "information_ratio": 0.5,
                "tracking_error": 0.04, "up_capture": 0.95, "down_capture": 0.8,
                "capture_ratio": 1.18,
            }
            legs_resp = []
            for leg in legs_req:
                entry: dict[str, Any] = {
                    "id": leg["id"], "symbol": "TEST", "result": self.backtest_result,
                }
                if reference is not None and leg.get("id") != reference:
                    entry["relative"] = relative
                legs_resp.append(entry)
            return httpx.Response(200, json={
                "status": "success",
                "run": {
                    "reference": reference,
                    "alignment": {
                        "legs": {
                            leg["id"]: {
                                "original_bars": N_BARS, "shared_bars": N_BARS,
                                "dropped_bars": 0,
                            }
                            for leg in legs_req
                        },
                        "shared_bars": N_BARS,
                        "effective_start": dates[0],
                        "effective_end": dates[-1],
                    },
                    "created_at": "2026-06-12T00:00:00+00:00",
                },
                "legs": legs_resp,
            })
        if path == "/api/backtest/export":
            return httpx.Response(
                200,
                content=b"PK\x03\x04fake-xlsx",
                headers={
                    "Content-Type": (
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    ),
                    "Content-Disposition": (
                        'attachment; filename="backtest360_export_test.xlsx"'
                    ),
                },
            )
        if path == "/api/latest-signal":
            return httpx.Response(200, json={"status": "success", "result": {
                "signal": 1, "bar_timestamp": "2026-06-11T00:00:00+00:00",
                "long_entry_fired": True, "long_exit_fired": False,
                "short_entry_fired": False, "short_exit_fired": False,
                "warmup_bars_used": 14,
            }})
        if path == "/api/stats":
            return httpx.Response(200, json={"status": "success",
                                             "stats": self.backtest_result["stats"]})
        if path == "/api/data/search":
            return httpx.Response(200, json={"status": "success", "count": 1, "results": [
                {"ticker": "BTC-USD", "name": "Bitcoin USD", "asset_class": "crypto"},
            ]})
        if path == "/api/data/tickers":
            return httpx.Response(200, json={"status": "success", "count": 1, "results": [
                {"ticker": "BTC-USD", "name": "Bitcoin USD", "asset_class": "crypto"},
            ]})
        if path == "/api/data/range":
            return httpx.Response(200, json={
                "symbol": "BTC-USD", "frequency": "daily",
                "available_from": "2014-09-17", "available_to": "2026-06-11",
                "estimated_bars": 4285,
            })
        if path == "/api/data/info":
            return httpx.Response(200, json={
                "status": "success", "symbol": "BTC-USD", "source": "test",
                "info": {
                    "ticker": "BTC-USD", "name": "Bitcoin USD",
                    "asset_class": "crypto", "exchange": "CRYPTO",
                    "currency": "USD", "active": True,
                },
                "coverage": {
                    "frequency": "daily", "available_from": "2014-09-17",
                    "available_to": "2026-06-11", "estimated_bars": 4285,
                    "is_24h": True,
                },
            })
        if path == "/api/data/quote":
            return httpx.Response(200, json={
                "status": "success", "symbol": "BTC-USD", "source": "test",
                "frequency": "daily", "as_of": "2026-06-11T00:00:00+00:00",
                "price": 68000.0, "open": 67000.0, "high": 68500.0,
                "low": 66900.0, "close": 68000.0, "volume": 1234.5,
            })
        if path == "/api/data/history":
            hist_dates = _dates(N_BARS)
            return httpx.Response(200, json={
                "status": "success",
                "summary": {
                    "symbol": "BTC-USD", "source": "test",
                    "start_date": hist_dates[0], "end_date": hist_dates[-1],
                    "total_bars": N_BARS, "frequency": "daily",
                    "columns": ["open", "high", "low", "close", "volume"],
                    "price_range": {"min": 90.0, "max": 110.0},
                    "has_gaps": False, "gap_count": 0,
                },
                "market_hours": {"is_24h": True, "regular_hours": None},
                "ohlcv": {
                    "dates": hist_dates,
                    "open": [100.0] * N_BARS, "high": [110.0] * N_BARS,
                    "low": [90.0] * N_BARS, "close": [105.0] * N_BARS,
                    "volume": [10.0] * N_BARS,
                },
            })
        if path == "/api/data/macro/series":
            return httpx.Response(200, json={
                "status": "success", "source": "test", "count": 2,
                "categories": ["rates", "inflation"],
                "series": [
                    {"id": "treasury_10y", "fred_id": "DGS10",
                     "title": "10-Year Treasury Yield", "category": "rates",
                     "frequency": "daily", "units": "percent",
                     "description": "Ten-year constant-maturity yield."},
                    {"id": "cpi", "fred_id": "CPIAUCSL",
                     "title": "Consumer Price Index", "category": "inflation",
                     "frequency": "monthly", "units": "index",
                     "description": "All urban consumers, all items."},
                ],
            })
        if path == "/api/data/samples":
            return httpx.Response(200, json={
                "status": "success", "symbols": ["SPY", "QQQ", "BTC"],
            })
        if path == "/api/data/sample":
            sample_dates = _dates(250)
            return httpx.Response(200, json={
                "status": "success",
                "summary": {
                    "symbol": "SPY", "source": "sample",
                    "start_date": sample_dates[0], "end_date": sample_dates[-1],
                    "total_bars": 250, "frequency": "daily",
                    "columns": ["open", "high", "low", "close", "volume"],
                    "price_range": {"min": 475.12, "max": 688.0},
                    "has_gaps": False, "gap_count": 0,
                },
                "market_hours": {"is_24h": False, "regular_hours": None},
                "ohlcv": {
                    "dates": sample_dates,
                    "open": [100.0] * 250, "high": [101.0] * 250,
                    "low": [99.0] * 250, "close": [100.5] * 250,
                    "volume": [1_000.0] * 250,
                },
            })
        if path.startswith("/api/data/lookup/"):
            ticker = path.removeprefix("/api/data/lookup/")
            return httpx.Response(200, json={
                "status": "success",
                "result": {
                    "ticker": ticker, "name": "Apple Inc.",
                    "asset_class": "stocks", "exchange": "XNAS",
                    "currency": "USD", "active": True,
                },
            })
        if path == "/api/data/macro":
            macro_dates = [d[:10] for d in _dates(800)]
            return httpx.Response(200, json={
                "status": "success", "source": "test",
                "series": {
                    "id": "treasury_10y", "fred_id": "DGS10",
                    "title": "10-Year Treasury Yield", "category": "rates",
                    "frequency": "daily", "units": "percent",
                    "description": "Ten-year constant-maturity yield.",
                },
                "as_of": macro_dates[-1], "count": len(macro_dates),
                "observations": {
                    "dates": macro_dates,
                    "values": [4.0 + (i % 20) * 0.01 for i in range(800)],
                },
            })
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})


@pytest.fixture
def mock_engine() -> MockEngine:
    return MockEngine()


@pytest.fixture
def settings() -> Settings:
    return Settings(engine_url="https://engine.test", api_key="b360_testkey")


@pytest.fixture
def engine(mock_engine: MockEngine, settings: Settings) -> EngineClient:
    return EngineClient(
        engine_url=settings.engine_url,
        api_key=settings.api_key,
        timeout=5.0,
        transport=httpx.MockTransport(mock_engine.handler),
    )
