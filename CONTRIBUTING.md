# Contributing

Thanks for using `backtest360-mcp` and for taking the time to help improve it.

## How contributions work here

`backtest360-mcp` is the official MCP server for the Backtest360 API. Its tool
surface tracks the Backtest360 engine, and every change is validated against
our release process before shipping.

For that reason this is an **issues-only** project: bug reports and feature
requests are very welcome, but we generally **do not accept pull requests**. The
best way to contribute is to open a clear issue — it goes straight onto our
roadmap and is released as a normal versioned update.

## Reporting a bug

Please use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). A
good report includes:

- **backtest360-mcp version** — `python -c "import backtest360_mcp; print(backtest360_mcp.__version__)"`
- **Python version and OS**
- A **minimal reproducible example** — the smallest snippet that triggers the issue
- The **full traceback**, if any

The more precise the report, the faster we can fix it.

## Requesting a feature

Please use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
Suggestions about the shape of the API — method names, arguments, return types —
are especially helpful. Show us how you'd like to call it.

## Dependency policy

Every dependency in `pyproject.toml` — runtime, optional groups, and build requirements —
carries an explicit upper bound: `>=<floor>,<<next-major>` for packages at 1.x or above,
and `<1` for 0.x packages (where the minor is the breaking-change unit). CI installs
dependencies fresh on every run, so a bare lower bound would silently adopt a
dependency's next major release the moment it publishes, without review.

Raising a cap is a deliberate, reviewed change, never a side effect of an unrelated PR:
bump the bound, run the full test suite against the new major, and migrate any code it
breaks — all in the same PR. Verify with a clean venv (`python3 -m venv` +
`pip install -e ".[dev]"`); an existing environment holding an older version can pass
while a fresh install fails.

## Security issues

Please **do not** open a public issue for security reports. See
[SECURITY.md](SECURITY.md) for how to report vulnerabilities privately.

## Questions and feedback

Have a question, or feedback that isn't a bug or feature request? Email us at
**hello@backtest360.com** — we read everything.
