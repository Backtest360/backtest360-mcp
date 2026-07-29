# backtest360-mcp

[![PyPI version](https://img.shields.io/pypi/v/backtest360-mcp.svg)](https://pypi.org/project/backtest360-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/backtest360-mcp.svg)](https://pypi.org/project/backtest360-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![tests](https://github.com/Backtest360/backtest360-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/Backtest360/backtest360-mcp/actions/workflows/test.yml)

MCP server exposing the [Backtest360](https://backtest360.com) engine API as tools for AI agents.

<!-- mcp-name: com.backtest360/backtest360 -->

Connect any MCP-capable AI client and drive real backtests conversationally: discover
indicators, build and validate strategies, run backtests, and read the results — all
against the deterministic Backtest360 engine. The server contains no AI and computes no
numbers of its own; it is a thin, faithful adapter over the engine HTTP API. Your engine
API key and its plan govern everything (permissions, rate limits, data access).

Two transports: a **hosted HTTP endpoint** at
`https://mcp.backtest360.com/mcp` (send your key as an `X-API-Key` header) and **local
stdio** (self-host — see below).

## Install

```bash
pip install backtest360-mcp        # or, from a clone: pip install -e .
```

Requires Python 3.10+ and a Backtest360 API key. Get one **free, instantly** at
[backtest360.com/api-access](https://backtest360.com/api-access) — submit your email and a key
(format `b360_…`) is issued on the spot and emailed to you; no approval needed. Authentication
is **API-key only**. The free tier runs backtests on data you upload; fetching historical price
data from the engine server-side is a paid capability.

## Configuration

Everything is environment-driven:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BACKTEST360_API_KEY` | yes | — | Engine API key, sent as `X-API-Key` |
| `BACKTEST360_ENGINE_URL` | no | `https://api.backtest360.com` | Engine base URL |
| `BACKTEST360_MCP_TIMEOUT` | no | `300` | Per-request timeout (seconds) |
| `BACKTEST360_MCP_MAX_OUTPUT_BYTES` | no | `100000` | Hard cap on a single tool result |

## Connect an MCP client

### Hosted (recommended)

Point your MCP client at the hosted endpoint over HTTP and send your key as an
`X-API-Key` header:

```json
{
  "mcpServers": {
    "backtest360": {
      "type": "streamable-http",
      "url": "https://mcp.backtest360.com/mcp",
      "headers": {
        "X-API-Key": "b360_..."
      }
    }
  }
}
```

### Local (stdio)

Run the server yourself and let your client launch it over stdio (the common
`mcpServers` shape):

```json
{
  "mcpServers": {
    "backtest360": {
      "command": "backtest360-mcp",
      "env": {
        "BACKTEST360_API_KEY": "b360_..."
      }
    }
  }
}
```

Prefer not to put the key in a config file? Point `command` at a small wrapper script
that exports the key from your secrets manager and then runs `backtest360-mcp`. A
minimal example config is in [`examples/mcp.json`](examples/mcp.json).

## Tools

| Tool | What it does |
|---|---|
| `engine_info` | Engine version, API contract, health |
| `get_me` | What the configured key can do: permission scopes, limits, current usage, capability flags |
| `get_catalog` | Reference catalogs: operators, execution modes, stop types, sizing methods, bar frequencies, metric sections |
| `list_indicators` | Indicator discovery; per-indicator parameter schemas |
| `list_templates` | Predesigned strategy templates — discover compactly, fetch one in full, ready to validate and run |
| `get_strategy_schema` | JSON Schema for strategy documents |
| `validate_strategy` | Validate a strategy without running it — returns structured, locatable errors |
| `run_backtest` | Run a historical backtest |
| `get_latest_signal` | Evaluate the most recent bar only (no P&L) |
| `compare_backtests` | Run several strategies on the same data, side by side |
| `compute_stats` | Compute the metric set from an externally produced returns series |
| `search_tickers` / `list_tickers` | Asset discovery for server-side data fetch |
| `get_data_range` | Available history and bar-count estimate for a symbol |
| `get_ticker_info` | Symbol identity and data coverage in a single call |
| `get_quote` | Latest available price for a symbol (paid plan) |
| `get_price_history` | OHLCV price history over a date range (paid plan; long histories downsampled to fit) |
| `list_macro_series` / `get_macro_series` | Macroeconomic data: list the series catalog, then fetch one series' observations |

The cheap static catalogs are also published as MCP resources
(`backtest360://catalog/{name}`, `backtest360://schema/strategy`) for clients that
support resource attachment.

## Prompts

Two workflow prompts scaffold the common multi-tool flows for a connected AI: each
names which tools to call, in what order, and what to look at in the results. They
carry no interpretation and compute nothing — the connected AI does the reasoning.

| Prompt | Arguments | What it scaffolds |
|---|---|---|
| `robustness_review` | `symbol`, `strategy` (optional) | Review a backtested strategy for robustness: validate → run → compare against buy-and-hold → weigh the evidence base (sample size, significance/robustness statistics, warnings) → caveated summary |
| `build_and_validate` | `idea` | Turn a plain-language idea into a validated strategy: survey the catalogs → fetch the schema → construct → validate-and-fix loop → dry-run |

## Response shaping

A full backtest result is megabytes; an agent's context is not. `run_backtest` and
`compare_backtests` take `response_detail`:

- `summary` (default) — headline metrics, warnings, counts, equity endpoints
- `stats` — every metric the plan allows
- `full` — plus series (downsampled, endpoints preserved) and trades (paginated)

`run_backtest` also takes `max_series_points` (default 500, must be >= 2) to
override the series downsampling cap — set it higher for full-resolution
series on a long run, or leave it unset for today's default.

`include=["trades", "equity_curve", "monthly_returns", "yearly_returns",
"signal_diagnostics"]` adds specific blocks at any detail level.
`signal_diagnostics` reports which per-bar entry/exit conditions fired, as a
capped list of fire dates per condition (not the raw per-bar boolean arrays,
which downsampling would corrupt) — or `{"available": false, ...}` when the
run has no condition tree to evaluate (e.g. precomputed signals). Results
exceeding the output cap are reduced further and explicitly marked
`truncated_by_mcp` — never silently cut. Shaping only ever selects and thins
what the engine returned; no value is computed or altered.

## Error semantics

Designed for agents:

- **Fixable by changing the request** → returned as a normal result: failed validations
  arrive as `{"valid": false, "errors": [...]}` with machine codes and document
  locations; engine rejections arrive as `{"accepted": false, "error": ...}` with a hint.
- **Not fixable that way** → a tool error with explicit guidance: rate limits carry the
  `Retry-After` value; engine-busy says retry with backoff; a compute timeout says
  do **not** retry and reduce scope instead; permission problems name the missing
  capability. Engine request ids are included for support.

## Running the tests (self-host)

```bash
pip install -e ".[dev]"
pytest   # unit suite vs a mock engine — no network
```

## Questions / feedback

Questions or feedback? hello@backtest360.com — we read everything. backtest360-mcp is
in active development, so help shape it.

Bug reports and feature requests: open an issue on GitHub.

## License

MIT — see [LICENSE](LICENSE).
