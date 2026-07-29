"""Workflow prompts — tool-ordering scaffolding for the connected AI.

These are static templates. They name which Backtest360 tools to call, in what
order, and what to look at in the results. They compute nothing, call no engine
endpoint, and carry no interpretive thresholds — the reasoning belongs to
whatever AI is connected. Every step here is inferable from the public tool
descriptions.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    """Register the workflow prompts on the server instance."""

    @mcp.prompt(title="Robustness review of a backtested strategy")
    def robustness_review(symbol: str, strategy: str | None = None) -> str:
        """Walk the connected AI through a rigorous robustness review of a
        backtested strategy on one symbol: validate, run, compare against
        buy-and-hold, weigh the evidence base (sample size, significance and
        robustness statistics, warnings), and report with caveats.

        Args:
            symbol: The asset the strategy trades (e.g. "BTC-USD").
            strategy: Optional strategy document (as JSON text) to review. If
                omitted, the prompt points at building or supplying one first.
        """
        if strategy:
            strategy_line = (
                "\n   The strategy document to review:\n" + strategy
            )
        else:
            strategy_line = (
                " If you do not have the strategy document yet, obtain or build "
                "it first (the build_and_validate prompt turns an idea into a "
                "validated document)."
            )
        return (
            f"Review a backtested Backtest360 strategy on {symbol} for "
            "robustness. Work through these steps in order, calling the "
            "Backtest360 tools, and base every statement on the numbers the "
            "engine returns — do not estimate or fill in values yourself.\n\n"
            "1. Validate first. Call validate_strategy on the strategy document "
            "and fix any reported errors before running anything."
            f"{strategy_line}\n"
            "2. Run the backtest. Call run_backtest with "
            'response_detail="summary" for the headline result, then '
            'response_detail="stats" for the full metric set.\n'
            f"3. Compare against buy-and-hold. Add a buy-and-hold benchmark on "
            f"{symbol} — run_backtest's benchmark option, or compare_backtests "
            "with include_benchmark=true — so the strategy can be read against "
            "simply holding the asset.\n"
            '4. Ground the metrics. Call get_catalog("sections") for the id, '
            "label, and description of each statistic before referring to it.\n"
            "5. Check the evidence, not just the headline return:\n"
            "   - Sample size: the number of trades and the length of the test "
            "window.\n"
            "   - Significance and robustness statistics, if the result "
            "includes them: the deflated Sharpe ratio, bootstrap confidence "
            "intervals, and any regime- or rolling-window breakdown.\n"
            "   - The strategy's result against the buy-and-hold benchmark, "
            "after costs.\n"
            "   - Every entry in the result's warnings list and its "
            "data-quality block.\n"
            "6. Write a caveated summary: state what the numbers show and note "
            "the limitations — sample size, single market regime, margin over "
            "buy-and-hold, and any active warnings."
        )

    @mcp.prompt(title="Build and validate a strategy from an idea")
    def build_and_validate(idea: str) -> str:
        """Walk the connected AI from a plain-language strategy idea to a
        validated Backtest360 strategy document, then a dry-run: survey the
        catalogs, fetch the document schema, construct the strategy, validate
        and fix in a loop until it passes, then smoke-test that it runs.

        Args:
            idea: The strategy idea in plain language (e.g. "buy when the
                50-day crosses above the 200-day, exit on the reverse cross").
        """
        return (
            "Turn an investing idea into a validated Backtest360 strategy, then "
            "dry-run it. The idea:\n"
            + idea
            + "\n\nGround every name and parameter in what the engine actually "
            "offers — never invent an indicator, operator, or parameter. Work "
            "in order:\n\n"
            "1. Survey what exists. Call list_indicators() to see the available "
            "indicators, then list_indicators(name=<id>) for the exact "
            'parameters of each one you plan to use, and get_catalog("operators") '
            "for the comparison operators. If a predesigned template may already "
            "match the idea, list_templates() (then list_templates(name=<id>)) "
            "gives a complete, runnable starting point to adapt.\n"
            "2. Get the document shape. Call get_strategy_schema() so the "
            "strategy document — indicators[] plus condition_tree — is "
            "structured correctly.\n"
            "3. Construct the strategy. Translate the idea into indicators[] and "
            "a condition_tree, using only the ids, operators, and parameters "
            "confirmed in steps 1-2.\n"
            "4. Validate and fix in a loop. Call validate_strategy. A failure "
            'returns {"valid": false, "errors": [...]} with a machine code, the '
            "document location, and context (such as the list of valid column "
            "names) for each problem — read them, correct the document, and "
            'validate again. Repeat until it returns {"valid": true}.\n'
            "5. Dry-run. Once valid, run a single small run_backtest with "
            'response_detail="summary" over a short date range or a small '
            "uploaded OHLCV sample, to confirm the strategy runs end to end and "
            "returns a result. This is a smoke test that the strategy executes, "
            "not an evaluation of it — use the robustness_review prompt to "
            "assess performance."
        )
