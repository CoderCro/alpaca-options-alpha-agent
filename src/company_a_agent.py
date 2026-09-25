"""Company A's deterministic decision loop.

No LangChain, no LLM origination or execution authority anywhere in this
file -- Featherless is reachable only through place_option_order.func's
existing internal veto call, same as Company B. Reuses agent_tools.py's
tool functions directly via their .func attribute (the raw undecorated
function every @tool wraps), so this gets the exact same guardrails, veto
gate, execution path, and audit trail as Company B for free -- the only
difference is that a mechanical rule, not an LLM, decides what to propose.
"""

import math

from src import agent_tools, execution, guardrails


def run_trading_cycle(tickers: list[str]) -> dict:
    actions = []

    for exit_action in agent_tools.check_exit_actions.func():
        result = agent_tools.close_or_trim_position.func(
            option_symbol=exit_action["symbol"],
            qty=exit_action["sell_qty"],
            limit_price=exit_action["current_price"],
            rationale=exit_action["reason"],
            next_stage=exit_action["next_stage"],
        )
        actions.append({"ticker": exit_action["symbol"], "action": "exit", "result": result})

    # Skip the whole entry scan at the position cap -- guardrails would reject
    # every entry anyway, and each ticker costs several bars/chain fetches.
    if len(agent_tools.get_positions.func()) >= guardrails.MAX_CONCURRENT_POSITIONS:
        actions.append({"action": "entries_skipped", "reason": f"at the {guardrails.MAX_CONCURRENT_POSITIONS}-position cap"})
        return {"summary": f"{len(actions)} tickers/exits evaluated", "actions": actions}

    for ticker in tickers:
        try:
            actions.append(_evaluate_entry(ticker))
        except execution.AlpacaCliError as e:
            # One ticker's failed data fetch must not abort the rest of the scan.
            actions.append({"ticker": ticker, "action": "data_error", "error": str(e)[:200]})

    return {"summary": f"{len(actions)} tickers/exits evaluated", "actions": actions}


def _evaluate_entry(ticker: str) -> dict:
    signal = agent_tools.get_signal.func(ticker)
    if not signal["qualifies_for_trading_list"] or signal["direction"] is None:
        return {"ticker": ticker, "action": "no_signal", "detail": signal}

    candidates = agent_tools.get_option_candidates.func(underlying_symbol=ticker, direction=signal["direction"])
    if not candidates:
        return {"ticker": ticker, "action": "no_candidates"}

    chosen = candidates[0]  # already sorted by proximity-to-ATM -- mechanical, not a placeholder
    equity = agent_tools.get_account_summary.func()["equity"]
    max_risk_usd = equity * guardrails.PER_TRADE_RISK_PCT / 100
    qty = max(1, math.floor(max_risk_usd / (chosen["ask"] * 100)))

    result = agent_tools.place_option_order.func(
        underlying_symbol=ticker,
        option_symbol=chosen["symbol"],
        qty=qty,
        limit_price=chosen["ask"],
        direction=signal["direction"],
        rationale=f"mechanical 2-of-4 gate: {signal['met']}",
    )
    return {"ticker": ticker, "action": "entry_attempt", "result": result}
