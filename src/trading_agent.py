"""LangChain agent wiring: Featherless as the reasoning model, this
codebase's tool surface (agent_tools.ALL_TOOLS) as its only means of
observing or acting on the world.

Hand-rolled bind_tools() + loop rather than AgentExecutor/create_agent/
langgraph -- one agent, a handful of tools, no multi-agent handoff or
persisted cross-run memory, so the heavier abstractions buy nothing here
and cost inspectability + mockability. A `model` injection param mirrors
featherless_review.review_candidate's `client` param, for the same reason:
tests can inject a fake model instead of hitting the real API.
"""

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src import featherless_review
from src.agent_tools import ALL_TOOLS

MAX_TOOL_TURNS = 6

SYSTEM_PROMPT = """You are the trading-decision agent for an autonomous options trading system \
running on a dedicated Alpaca paper account. You decide whether and what to trade -- you are not a \
mechanical rule-follower, use your judgment -- but every order you place is independently re-checked \
by hard risk/compliance gates and a second AI review before it reaches the market, and some things \
are non-negotiable no matter what you decide:

- Only single-leg, long calls or puts (buy-to-open). No spreads, no short/naked structures.
- Only tickers on the approved watchlist -- place_option_order will refuse anything off-list.
- Check existing positions for required exit actions (check_exit_actions) before considering new entries.
- Use get_signal to ground any new entry in the technical picture (support/resistance, multi-timeframe \
trend, MA-as-S&R, monthly-vs-weekly-MA10) -- you don't need all four criteria met, but be able to \
explain your reasoning in the `rationale` you pass to place_option_order, since it's recorded in the \
audit trail.
- If nothing is compelling, it's completely fine to take no action this cycle.

Work through the tools available to you, then either place/close orders as warranted or conclude with \
no action. When you're done for this cycle, summarize what you did (or didn't do) and why in plain text."""

_TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}


def _build_model(model: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model or os.environ.get("FEATHERLESS_MODEL") or featherless_review.DEFAULT_MODEL,
        base_url=featherless_review.FEATHERLESS_BASE_URL,
        api_key=os.environ["FEATHERLESS_API_KEY"],
        default_headers={"Accept-Encoding": "identity"},
        temperature=0.2,
    )


def run_trading_cycle(tickers: list[str], model=None) -> dict:
    """Runs one decision cycle over the given tickers.

    Returns {"summary": str, "messages": list, "ran_out_of_turns": bool} --
    the full message trace is the reasoning-trail data for the audit
    log/dashboard. Running out of tool-call turns is treated as "no trade"
    (fail-closed), consistent with the rest of the system.
    """
    llm = (model or _build_model()).bind_tools(ALL_TOOLS)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Evaluate these tickers this cycle: {', '.join(tickers)}."),
    ]

    for _ in range(MAX_TOOL_TURNS):
        response = llm.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            return {"summary": response.content, "messages": messages, "ran_out_of_turns": False}

        for call in response.tool_calls:
            tool_fn = _TOOLS_BY_NAME.get(call["name"])
            result = {"error": f"unknown tool {call['name']!r}"} if tool_fn is None else tool_fn.invoke(call["args"])
            messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

    return {
        "summary": "ran out of tool-call turns without a final answer -- no action taken",
        "messages": messages,
        "ran_out_of_turns": True,
    }
