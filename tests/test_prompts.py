"""Prompt-level behavior through a real MCPServer instance against the mock engine.

Prompts are static workflow scaffolding — they name tools and an order, compute
nothing, and touch no engine endpoint. These tests check they are registered,
render with their arguments, and stay coupled to the tools they orchestrate (so
a future tool rename can't leave the scaffolding silently stale).
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

from backtest360_mcp.tools import register_all

STRATEGY_JSON = '{"name": "sma-cross", "indicators": [], "condition_tree": {}}'


@pytest.fixture
def server(engine, settings) -> MCPServer:
    mcp = MCPServer("backtest360-test")
    register_all(mcp, engine, settings)
    return mcp


async def render(server: MCPServer, name: str, arguments: dict) -> str:
    """Render a prompt and return the concatenated text of its messages."""
    result = await server.get_prompt(name, arguments)
    return "\n".join(msg.content.text for msg in result.messages)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

EXPECTED_PROMPTS = {"robustness_review", "build_and_validate"}


async def test_all_prompts_registered(server):
    prompts = {p.name for p in await server.list_prompts()}
    assert prompts == EXPECTED_PROMPTS


async def test_every_prompt_has_description(server):
    for prompt in await server.list_prompts():
        assert prompt.description and len(prompt.description) > 40, prompt.name


async def test_prompt_arguments(server):
    by_name = {p.name: p for p in await server.list_prompts()}

    review_args = {a.name: a.required for a in by_name["robustness_review"].arguments}
    assert review_args == {"symbol": True, "strategy": False}

    build_args = {a.name: a.required for a in by_name["build_and_validate"].arguments}
    assert build_args == {"idea": True}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

async def test_robustness_review_without_strategy(server):
    text = await render(server, "robustness_review", {"symbol": "BTC-USD"})
    assert "BTC-USD" in text
    # No document supplied → the prompt points at building/supplying one.
    assert "build_and_validate" in text


async def test_robustness_review_with_strategy(server):
    text = await render(
        server, "robustness_review",
        {"symbol": "ETH-USD", "strategy": STRATEGY_JSON},
    )
    assert "ETH-USD" in text
    assert STRATEGY_JSON in text  # the supplied document is embedded verbatim


async def test_robustness_review_missing_required_arg(server):
    with pytest.raises(ValueError):
        await server.get_prompt("robustness_review", {})


async def test_build_and_validate_embeds_idea(server):
    idea = "buy when the 50-day SMA crosses above the 200-day SMA"
    text = await render(server, "build_and_validate", {"idea": idea})
    assert idea in text
    # The fix-and-retry loop is the point of this prompt.
    assert "validate_strategy" in text


# ---------------------------------------------------------------------------
# prompt <-> tool coupling — scaffolding must not name tools that don't exist
# ---------------------------------------------------------------------------

# The core tools each prompt orchestrates by name in its text. If a tool is
# renamed, both halves of each assertion below break together — the prompt text
# can never silently drift away from the real tool surface.
_PROMPT_TOOLS = {
    ("robustness_review", ("symbol",)): (
        "validate_strategy", "run_backtest", "compare_backtests", "get_catalog",
    ),
    ("build_and_validate", ("idea",)): (
        "list_indicators", "get_catalog", "list_templates",
        "get_strategy_schema", "validate_strategy", "run_backtest",
    ),
}


async def test_prompts_only_name_real_tools(server):
    registered = {t.name for t in await server.list_tools()}
    for (name, arg_names), tools in _PROMPT_TOOLS.items():
        arguments = {a: "x" for a in arg_names}
        text = await render(server, name, arguments)
        for tool in tools:
            assert tool in registered, f"{name} names unknown tool {tool!r}"
            assert tool in text, f"{name} no longer mentions {tool!r}"
