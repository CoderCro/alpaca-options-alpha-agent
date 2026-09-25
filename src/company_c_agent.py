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

from src import agent_tools, delta_hedge, execution, guardrails


def run_trading_cycle(tickers: list[str]) -> dict:
    actions = []

    for exit_action in agent_tools.check_vol_edge_exit_actions.func():
        result = agent_tools.close_delta_neutral_position.func(
            put_symbol=exit_action["put_symbol"],
            put_qty=exit_action["put_qty"],
            put_limit_price=exit_action["put_current_price"],
            hedge_shares=exit_action["hedge_shares"],
            # Stock orders need whole-cent prices; hedge_current_price can
            # fall back to the raw multi-decimal bar close (see
            # get_vol_edge_signal's "spot") when no live position quote is
            # available. Live-confirmed Sep 2: Alpaca rejects a sub-penny
            # stock limit price outright ("sub-penny increment does not
            # fulfill minimum pricing criteria"), which silently left 5 of
            # 8 entries with a filled put and no hedge at all.
            hedge_limit_price=round(exit_action["hedge_current_price"], 2),
            rationale=f"vol-edge exit: {exit_action['reason']}",
        )
        actions.append({"ticker": exit_action["underlying_symbol"], "action": "exit", "result": result})

    for ticker in tickers:
        try:
            actions.append(_evaluate_entry(ticker))
        except execution.AlpacaCliError as e:
            # One ticker's failed data fetch must not abort the rest of the scan.
            actions.append({"ticker": ticker, "action": "data_error", "error": str(e)[:200]})

    return {"summary": f"{len(actions)} tickers/exits evaluated", "actions": actions}


def _evaluate_entry(ticker: str) -> dict:
    signal = agent_tools.get_vol_edge_signal.func(ticker)
    if not signal.get("has_signal"):
        return {"ticker": ticker, "action": "no_signal", "detail": signal}

    candidate = signal["candidate"]
    equity = agent_tools.get_account_summary.func()["equity"]
    max_risk_usd = equity * guardrails.per_trade_risk_pct() / 100

    # Delta doesn't depend on qty -- probe it once (qty=1 is a throwaway)
    # so sizing can account for the hedge leg's notional, not just the
    # put's own premium. A cheap 1-3 DTE put's premium is a small
    # fraction of the underlying's price, so its delta-hedge (which
    # covers the full 100-shares-per-contract notional, scaled by
    # delta) is routinely far bigger in dollar terms than the premium.
    # Live-confirmed on DIS: a $2,987 put sized to the cap needed a
    # $179,373 hedge, 60x over the same cap -- sizing on premium alone
    # was silently making every single entry unplaceable, not
    # occasionally too big.
    delta_probe = delta_hedge.compute_hedge(
        spot=signal["spot"], strike=candidate["strike"], years=signal["years_to_expiry"],
        vol=signal["implied_vol"], option_type="put", option_qty=1,
    )
    put_qty_from_premium = max_risk_usd / (candidate["ask"] * 100)
    put_qty_from_hedge = max_risk_usd / (abs(delta_probe.option_delta) * 100 * signal["spot"])
    put_qty = math.floor(min(put_qty_from_premium, put_qty_from_hedge))
    if put_qty < 1:
        # Even a single contract's hedge notional exceeds the risk cap
        # at this underlying's price -- forcing qty=1 anyway would just
        # get rejected by guardrails' own risk_cap gate on the hedge
        # leg. Skip cleanly instead of attempting a doomed trade.
        return {"ticker": ticker, "action": "hedge_unaffordable", "detail": signal}

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
        # Round to a valid stock tick size -- signal["spot"] is the raw
        # multi-decimal bar close (get_vol_edge_signal), and Alpaca
        # rejects a sub-penny stock limit price outright. See the
        # matching note on the exit path above.
        hedge_limit_price=round(signal["spot"], 2),
        realized_vol=signal["realized_vol"],
        implied_vol=signal["implied_vol"],
        rationale=(
            f"vol edge {signal['edge']:.4f} "
            f"(realized {signal['realized_vol']:.4f} vs implied {signal['implied_vol']:.4f})"
        ),
    )
    return {"ticker": ticker, "action": "entry_attempt", "result": result}
