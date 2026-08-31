"""Company C's deterministic decision loop: delta-neutral vol-edge trading.

No LangChain, no LLM origination or execution authority anywhere in this
file -- Featherless is reachable only through place_delta_neutral_put.func's
existing internal veto call, same as Company A and B. Reuses agent_tools.py's
tool functions directly via their .func attribute, so this gets the exact
same guardrails, veto gate, execution path, and audit trail as A/B for free
-- only the signal (vol edge, not the 2-of-4 technical criteria) and the
sizing (delta-hedged) differ.
"""

import math

from src import agent_tools, delta_hedge, guardrails


def run_trading_cycle(tickers: list[str]) -> dict:
    actions = []

    for exit_action in agent_tools.check_vol_edge_exit_actions.func():
        result = agent_tools.close_delta_neutral_position.func(
            put_symbol=exit_action["put_symbol"],
            put_qty=exit_action["put_qty"],
            put_limit_price=exit_action["put_current_price"],
            hedge_shares=exit_action["hedge_shares"],
            hedge_limit_price=exit_action["hedge_current_price"],
            rationale=f"vol-edge exit: {exit_action['reason']}",
        )
        actions.append({"ticker": exit_action["underlying_symbol"], "action": "exit", "result": result})

    for ticker in tickers:
        signal = agent_tools.get_vol_edge_signal.func(ticker)
        if not signal.get("has_signal"):
            actions.append({"ticker": ticker, "action": "no_signal", "detail": signal})
            continue

        candidate = signal["candidate"]
        equity = agent_tools.get_account_summary.func()["equity"]
        max_risk_usd = equity * guardrails.PER_TRADE_RISK_PCT / 100
        put_qty = max(1, math.floor(max_risk_usd / (candidate["ask"] * 100)))

        hedge = delta_hedge.compute_hedge(
            spot=signal["spot"],
            strike=candidate["strike"],
            years=signal["years_to_expiry"],
            vol=signal["implied_vol"],
            option_type="put",
            option_qty=put_qty,
        )

        result = agent_tools.place_delta_neutral_put.func(
            underlying_symbol=ticker,
            put_symbol=candidate["symbol"],
            put_qty=put_qty,
            put_limit_price=candidate["ask"],
            hedge_shares=hedge.hedge_shares,
            hedge_limit_price=signal["spot"],
            realized_vol=signal["realized_vol"],
            implied_vol=signal["implied_vol"],
            rationale=(
                f"vol edge {signal['edge']:.4f} "
                f"(realized {signal['realized_vol']:.4f} vs implied {signal['implied_vol']:.4f})"
            ),
        )
        actions.append({"ticker": ticker, "action": "entry_attempt", "result": result})

    return {"summary": f"{len(actions)} tickers/exits evaluated", "actions": actions}
