"""The LangChain-callable tool surface for the trading agent.

This is the actual security boundary (see guardrails.py's docstring): every
gated write tool below calls guardrails.pre_trade_check unconditionally
before execution.submit_order is ever reached, and place_option_order
additionally runs featherless_review.review_candidate as a second,
independent veto pass. No tool here gives the agent raw shell/code access --
only these specific, purpose-built functions.

Read-only tools fetch live state from execution.py and reuse the existing
pure modules (rules_engine, position_manager, calendar_blackout) as-is.
"""

import json
from datetime import date, datetime, timedelta

import pandas as pd
from langchain_core.tools import tool

from src import (
    audit_log,
    company_config,
    execution,
    featherless_review,
    guardrails,
    hedge_store,
    options_math,
    options_selector,
    position_store,
    rules_engine,
    vol_edge,
    watchlist,
)
from src.calendar_blackout import NY_TZ, is_trading_allowed
from src.position_manager import Stage, next_action


def _position_max_risk(position: dict) -> float:
    if "cost_basis" in position:
        return abs(float(position["cost_basis"]))
    # Fallback only (cost_basis is normally present). Company C is the first
    # thing to ever hold a plain equity position (its delta hedge) alongside
    # options here, so the x100 contract multiplier below must not apply to
    # it -- option symbols are OCC-format and always contain digits (date +
    # strike); equity tickers never do.
    qty_price = abs(float(position.get("qty", 0)) * float(position.get("avg_entry_price", 0)))
    is_option = any(char.isdigit() for char in position.get("symbol", ""))
    return qty_price * 100 if is_option else qty_price


def _count_todays_open_entries() -> int:
    today = date.today().isoformat()
    path = company_config.log_dir() / f"audit_{today}.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("event_type") == "order_submitted" and record.get("intent") == "open":
            count += 1
    return count


def _fetch_ohlc(symbol: str, timeframe: str, start: str) -> pd.DataFrame:
    # "/" marks a crypto pair (e.g. "BTC/USD") -- different CLI subcommand and
    # response shape (bars keyed by symbol) than stock bars (a flat list).
    # feed="iex" avoids "subscription does not permit querying recent SIP
    # data" on accounts without a paid real-time SIP subscription.
    if "/" in symbol:
        response = execution.get_crypto_bars(symbol, start, timeframe=timeframe)
        bars = response.get("bars", {}).get(symbol, [])
    else:
        response = execution.get_bars(symbol, start, timeframe=timeframe, feed="iex")
        bars = response.get("bars", [])
    return pd.DataFrame(
        {
            "open": [b["o"] for b in bars],
            "high": [b["h"] for b in bars],
            "low": [b["l"] for b in bars],
            "close": [b["c"] for b in bars],
        }
    )


def _evaluate_signal(ticker: str, as_of: date | None = None):
    as_of = as_of or date.today()
    weekly_df = _fetch_ohlc(ticker, "1Week", (as_of - timedelta(weeks=80)).isoformat())
    daily_df = _fetch_ohlc(ticker, "1Day", (as_of - timedelta(days=180)).isoformat())
    h4_df = _fetch_ohlc(ticker, "4Hour", (as_of - timedelta(days=30)).isoformat())
    m15_df = _fetch_ohlc(ticker, "15Min", (as_of - timedelta(days=7)).isoformat())
    monthly_df = _fetch_ohlc(ticker, "1Month", (as_of - timedelta(days=120)).isoformat())
    return rules_engine.evaluate_criteria(
        weekly_df=weekly_df, daily_df=daily_df, h4_df=h4_df, m15_df=m15_df, monthly_df=monthly_df,
    )


# ---------------------------------------------------------------- read-only


@tool
def get_account_summary() -> dict:
    """Get current account equity, buying power, and options trading level."""
    account = execution.get_account()
    return {
        "equity": float(account["equity"]),
        "buying_power": float(account["buying_power"]),
        "options_trading_level": account.get("options_trading_level"),
    }


@tool
def get_positions() -> list[dict]:
    """List all currently open positions."""
    return execution.list_positions()


@tool
def check_blackout() -> dict:
    """Check whether trading is currently allowed under the blackout calendar
    (2h around the market open, and FOMC/NFP news days). Advisory only --
    order tools enforce this independently and will refuse regardless of
    what this returns.
    """
    allowed, reason = is_trading_allowed(datetime.now(NY_TZ))
    return {"allowed": allowed, "reason": reason}


@tool
def get_option_candidates(underlying_symbol: str, direction: str) -> list[dict]:
    """Get a short, pre-filtered shortlist of tradeable option contracts.

    Only a contract symbol from this shortlist can be passed to
    place_option_order -- the raw option chain is never exposed directly.

    Args:
        underlying_symbol: the underlying stock/ETF ticker, e.g. "AAPL".
        direction: "bullish" (returns calls) or "bearish" (returns puts).
    """
    today = date.today()
    bars = execution.get_bars(underlying_symbol, (today - timedelta(days=5)).isoformat(), timeframe="1Day")
    underlying_price = bars["bars"][-1]["c"]
    chain = execution.get_option_chain(
        underlying_symbol,
        expiration_gte=(today + timedelta(days=21)).isoformat(),
        expiration_lte=(today + timedelta(days=45)).isoformat(),
    )
    candidates = options_selector.select_option_candidates(
        chain.get("snapshots", {}), direction, underlying_price, as_of=today
    )
    return [
        {
            "symbol": c.symbol,
            "expiry": c.expiry.isoformat(),
            "strike": c.strike,
            "dte": c.dte,
            "moneyness": round(c.moneyness, 3),
            "bid": c.bid,
            "ask": c.ask,
            "delta": c.delta,
        }
        for c in candidates
    ]


@tool
def get_signal(ticker: str) -> dict:
    """Get the 2-of-4 technical-criteria signal for a ticker: support &
    resistance, multi-timeframe trend alignment, MA-as-support/resistance,
    and monthly-vs-weekly-MA10.
    """
    result = _evaluate_signal(ticker)
    return {
        "met": result.met,
        "details": result.details,
        "count_met": result.count_met,
        "qualifies_for_trading_list": result.qualifies_for_trading_list,
        "direction": result.direction,
    }


@tool
def check_exit_actions() -> list[dict]:
    """Check every tracked open position's exit ladder for a required action
    (scaled profit-taking, breakeven stop, or the hard days-to-expiry close).
    Returns an empty list if nothing needs action right now.
    """
    tracked = position_store.load_all()
    live_by_symbol = {p["symbol"]: p for p in execution.list_positions() if "symbol" in p}
    actions = []
    for symbol, position in tracked.items():
        live = live_by_symbol.get(symbol)
        if live is None:
            continue
        current_price = float(live.get("current_price", position.entry_price))
        _root, expiry, _option_type, _strike = options_selector.parse_occ_symbol(symbol)
        dte = (expiry - date.today()).days
        action = next_action(position, current_price, dte)
        if action is not None:
            actions.append(
                {
                    "symbol": symbol,
                    "sell_qty": action.sell_qty,
                    "reason": action.reason,
                    "next_stage": action.next_stage.name,
                    "current_price": current_price,
                }
            )
    return actions


@tool
def recommend_watchlist_addition(ticker: str, rationale: str) -> dict:
    """Recommend adding a ticker to the approved watchlist. This only adds it
    to a pending list for human review -- it does NOT make the ticker
    immediately tradeable.

    Args:
        ticker: the stock/ETF/crypto ticker to recommend.
        rationale: why this ticker fits the watchlist criteria.
    """
    result = watchlist.recommend_addition(ticker)
    audit_log.log_event("tool_call", tool="recommend_watchlist_addition", ticker=ticker, rationale=rationale)
    return {"pending": sorted(result.pending)}


# ------------------------------------------------------------- gated writes


@tool
def place_option_order(
    underlying_symbol: str,
    option_symbol: str,
    qty: int,
    limit_price: float,
    direction: str,
    rationale: str,
) -> dict:
    """Place a new long call or put option order (buy-to-open, single-leg only).

    Use this only to OPEN a brand-new position on a contract returned by a
    prior get_option_candidates call -- to exit or trim a position you
    already hold, use close_or_trim_position instead. Every request passes
    through hard risk/compliance gates and a second, independent AI review
    before anything reaches Alpaca; a rejected request returns
    {"placed": false, "reason": "..."} instead of raising.

    Args:
        underlying_symbol: the underlying stock/ETF ticker, e.g. "AAPL".
        option_symbol: the OCC contract symbol from get_option_candidates.
        qty: number of contracts to buy.
        limit_price: limit price per contract, in dollars.
        direction: "bullish" (long call) or "bearish" (long put).
        rationale: your reasoning for this trade -- recorded in the audit trail.
    """
    order = guardrails.OrderRequest(
        underlying_symbol=underlying_symbol,
        option_symbol=option_symbol,
        qty=qty,
        side="buy",
        intent="open",
        limit_price=limit_price,
    )
    account = execution.get_account()
    positions = execution.list_positions()
    equity = float(account["equity"])
    last_equity = float(account["last_equity"])
    daily_pnl_pct = (equity - last_equity) / last_equity * 100 if last_equity else 0.0
    open_positions_risk_usd = sum(_position_max_risk(p) for p in positions)
    approved_watchlist = watchlist.load().approved

    allowed, reason, gate = guardrails.pre_trade_check(
        order,
        account_equity_usd=equity,
        open_positions_risk_usd=open_positions_risk_usd,
        open_positions_count=len(positions),
        daily_new_entries_count=_count_todays_open_entries(),
        daily_pnl_pct=daily_pnl_pct,
        watchlist=approved_watchlist,
    )
    audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=option_symbol)
    if not allowed:
        return {"placed": False, "reason": reason, "gate": gate}

    candidate = featherless_review.TradeCandidate(
        ticker=underlying_symbol,
        direction=direction,
        criteria_met=[],
        signal_details={"rationale": rationale},
        proposed_structure=f"Long {option_symbol}",
        max_risk_usd=order.max_risk_usd,
        account_equity_usd=equity,
    )
    verdict = featherless_review.review_candidate(candidate)
    audit_log.log_event(
        "featherless_verdict",
        veto=verdict.veto,
        confidence=verdict.confidence,
        rationale=verdict.rationale,
        symbol=option_symbol,
    )
    if verdict.veto:
        return {"placed": False, "reason": f"Featherless veto: {verdict.rationale}", "gate": "featherless_veto"}

    audit_log.log_event(
        "order_submitted", intent="open", symbol=option_symbol, qty=qty, side="buy", limit_price=limit_price
    )
    try:
        result = execution.submit_order(option_symbol, qty, "buy", limit_price=limit_price)
    except execution.AlpacaCliError as e:
        audit_log.log_event("order_result", symbol=option_symbol, status="error", error=str(e))
        return {"placed": False, "reason": f"Alpaca CLI error: {e}", "gate": "execution_error"}

    position_store.record_new_position(option_symbol, limit_price, qty)
    audit_log.log_event("order_result", symbol=option_symbol, status="submitted", order_id=result.get("id"))
    return {"placed": True, "order": result}


@tool
def close_or_trim_position(
    option_symbol: str, qty: int, limit_price: float, rationale: str, next_stage: str | None = None
) -> dict:
    """Sell to close or trim an existing long option position you already hold.

    qty must not exceed how much of this exact contract Alpaca shows you
    currently holding -- oversized requests are rejected to prevent
    accidentally opening a naked short. Not subject to blackout, watchlist,
    or risk-cap checks (only the kill switch and the held-qty check), since
    cutting risk should never be blocked by the same limits that guard
    opening new risk.

    Args:
        option_symbol: the OCC contract symbol of the position to close/trim.
        qty: number of contracts to sell.
        limit_price: limit price per contract, in dollars.
        rationale: your reasoning -- recorded in the audit trail.
        next_stage: optional exit-ladder stage name (from check_exit_actions'
            next_stage field) to record after this sell.
    """
    positions = execution.list_positions()
    held = next((p for p in positions if p.get("symbol") == option_symbol), None)
    held_qty = int(float(held["qty"])) if held else 0
    underlying_symbol, _expiry, _option_type, _strike = options_selector.parse_occ_symbol(option_symbol)

    order = guardrails.OrderRequest(
        underlying_symbol=underlying_symbol,
        option_symbol=option_symbol,
        qty=qty,
        side="sell",
        intent="close",
        limit_price=limit_price,
        held_qty=held_qty,
    )
    allowed, reason, gate = guardrails.pre_trade_check(
        order,
        account_equity_usd=0.0,
        open_positions_risk_usd=0.0,
        open_positions_count=0,
        daily_new_entries_count=0,
        daily_pnl_pct=0.0,
        watchlist=set(),
    )
    audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=option_symbol)
    if not allowed:
        return {"placed": False, "reason": reason, "gate": gate}

    audit_log.log_event(
        "order_submitted", intent="close", symbol=option_symbol, qty=qty, side="sell", limit_price=limit_price
    )
    try:
        result = execution.submit_order(option_symbol, qty, "sell", limit_price=limit_price)
    except execution.AlpacaCliError as e:
        audit_log.log_event("order_result", symbol=option_symbol, status="error", error=str(e))
        return {"placed": False, "reason": f"Alpaca CLI error: {e}", "gate": "execution_error"}

    tracked = position_store.load_all()
    if option_symbol in tracked:
        pos = tracked[option_symbol]
        new_remaining = max(0, pos.remaining_qty - qty)
        stage = Stage[next_stage] if next_stage else (Stage.CLOSED if new_remaining == 0 else pos.stage)
        position_store.update_after_exit(option_symbol, new_remaining, stage)

    audit_log.log_event("order_result", symbol=option_symbol, status="submitted", order_id=result.get("id"))
    return {"placed": True, "order": result}


@tool
def cancel_open_order(order_id: str) -> dict:
    """Cancel an open, unfilled order by its Alpaca order ID.

    Args:
        order_id: the Alpaca order ID to cancel.
    """
    killed, reason = guardrails.kill_switch_active()
    if killed:
        audit_log.log_event("gate_result", gate="kill_switch", allowed=False, reason=reason, order_id=order_id)
        return {"cancelled": False, "reason": reason}
    try:
        execution.cancel_order(order_id)
    except execution.AlpacaCliError as e:
        audit_log.log_event("order_result", order_id=order_id, status="cancel_error", error=str(e))
        return {"cancelled": False, "reason": str(e)}
    audit_log.log_event("order_result", order_id=order_id, status="cancelled")
    return {"cancelled": True}


# ------------------------------------------------- Company C: vol-edge tools
#
# Only ever proposes puts, never calls: a delta-hedged put needs a stock BUY
# to hedge (put delta is negative), a delta-hedged call would need a stock
# SHORT -- guardrails.ALLOWED_SIDES_FOR_OPEN has no short-sale path, and by
# put-call parity a delta-hedged put carries essentially the same vol
# exposure as a delta-hedged call, so nothing is given up by only using puts.


@tool
def get_vol_edge_signal(underlying_symbol: str) -> dict:
    """Get Company C's vol-edge signal for a ticker: realized vol (from price
    history) vs. implied vol (solved from the live chain's quoted price,
    never trusted off the feed's own greeks -- see options_math.py). Also
    returns the nearest-the-money put candidate this signal would trade, if any.

    Args:
        underlying_symbol: the underlying stock/ETF ticker, e.g. "SPY".
    """
    today = date.today()
    daily_df = _fetch_ohlc(underlying_symbol, "1Day", (today - timedelta(days=60)).isoformat())
    closes = daily_df["close"].tolist()
    realized_vol = vol_edge.realized_volatility(closes)
    if realized_vol is None:
        return {"has_signal": False, "reason": "not enough price history yet"}

    spot = closes[-1]
    chain = execution.get_option_chain(
        underlying_symbol,
        expiration_gte=(today + timedelta(days=21)).isoformat(),
        expiration_lte=(today + timedelta(days=45)).isoformat(),
    )
    candidates = options_selector.select_option_candidates(chain.get("snapshots", {}), "bearish", spot, as_of=today)
    if not candidates:
        return {"has_signal": False, "reason": "no put candidates in the DTE/moneyness window"}

    chosen = candidates[0]  # already sorted by proximity-to-ATM
    if not chosen.bid or not chosen.ask:
        return {"has_signal": False, "reason": f"no usable quote on {chosen.symbol}"}
    mid_price = (chosen.bid + chosen.ask) / 2
    years = chosen.dte / 365
    implied_vol = options_math.implied_volatility(mid_price, spot, chosen.strike, years, "put")
    if implied_vol is None:
        return {"has_signal": False, "reason": f"could not solve implied vol from quote (bid={chosen.bid}, ask={chosen.ask})"}

    edge = vol_edge.evaluate_edge(realized_vol, implied_vol)
    return {
        "has_signal": edge.cheap_enough,
        "realized_vol": round(realized_vol, 4),
        "implied_vol": round(implied_vol, 4),
        "edge": round(edge.edge, 4),
        "spot": spot,
        "years_to_expiry": years,
        "candidate": {
            "symbol": chosen.symbol,
            "strike": chosen.strike,
            "dte": chosen.dte,
            "bid": chosen.bid,
            "ask": chosen.ask,
        },
    }


@tool
def place_delta_neutral_put(
    underlying_symbol: str,
    put_symbol: str,
    put_qty: int,
    put_limit_price: float,
    hedge_shares: int,
    hedge_limit_price: float,
    realized_vol: float,
    implied_vol: float,
    rationale: str,
) -> dict:
    """Open a new delta-neutral position: buy-to-open a put, then buy shares
    of the underlying to hedge its delta to ~0 (see delta_hedge.py).

    Both legs pass guardrails independently; the combined structure passes
    one Featherless veto call. If the put leg fills but the hedge leg then
    fails, the position is left temporarily unhedged -- logged loudly as a
    "hedge_leg_failed" audit event, the same "flag it, don't hide it"
    approach as the vertical-spread gap noted in execution.py, not a solved
    problem.

    Args:
        underlying_symbol: the underlying stock/ETF ticker, e.g. "SPY".
        put_symbol: the OCC contract symbol from get_vol_edge_signal.
        put_qty: number of put contracts to buy.
        put_limit_price: limit price per put contract, in dollars.
        hedge_shares: number of underlying shares to buy for the delta hedge.
        hedge_limit_price: limit price per share.
        realized_vol: the realized vol from get_vol_edge_signal (recorded for the exit check).
        implied_vol: the implied vol from get_vol_edge_signal (recorded for the exit check).
        rationale: your reasoning for this trade -- recorded in the audit trail.
    """
    account = execution.get_account()
    positions = execution.list_positions()
    equity = float(account["equity"])
    last_equity = float(account["last_equity"])
    daily_pnl_pct = (equity - last_equity) / last_equity * 100 if last_equity else 0.0
    open_positions_risk_usd = sum(_position_max_risk(p) for p in positions)
    approved_watchlist = watchlist.load().approved
    daily_new_entries_count = _count_todays_open_entries()

    put_order = guardrails.OrderRequest(
        underlying_symbol=underlying_symbol, option_symbol=put_symbol, qty=put_qty,
        side="buy", intent="open", limit_price=put_limit_price, asset_class="option",
    )
    allowed, reason, gate = guardrails.pre_trade_check(
        put_order, account_equity_usd=equity, open_positions_risk_usd=open_positions_risk_usd,
        open_positions_count=len(positions), daily_new_entries_count=daily_new_entries_count,
        daily_pnl_pct=daily_pnl_pct, watchlist=approved_watchlist,
    )
    audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=put_symbol)
    if not allowed:
        return {"placed": False, "reason": reason, "gate": gate}

    # Hedge leg checked independently, on top of the put's own risk -- each
    # leg bounded by the same per-trade cap rather than a combined budget,
    # simpler and more conservative than modeling net offsetting risk.
    hedge_order = guardrails.OrderRequest(
        underlying_symbol=underlying_symbol, option_symbol=underlying_symbol, qty=hedge_shares,
        side="buy", intent="open", limit_price=hedge_limit_price, asset_class="equity",
    )
    allowed, reason, gate = guardrails.pre_trade_check(
        hedge_order, account_equity_usd=equity,
        open_positions_risk_usd=open_positions_risk_usd + put_order.max_risk_usd,
        open_positions_count=len(positions), daily_new_entries_count=daily_new_entries_count,
        daily_pnl_pct=daily_pnl_pct, watchlist=approved_watchlist,
    )
    audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=underlying_symbol)
    if not allowed:
        return {"placed": False, "reason": reason, "gate": gate}

    candidate = featherless_review.TradeCandidate(
        ticker=underlying_symbol,
        direction="bearish",
        criteria_met=[],
        signal_details={"realized_vol": f"{realized_vol:.4f}", "implied_vol": f"{implied_vol:.4f}", "rationale": rationale},
        proposed_structure=f"Long {put_qty}x {put_symbol} + delta-hedge {hedge_shares} shares of {underlying_symbol}",
        max_risk_usd=put_order.max_risk_usd,
        account_equity_usd=equity,
    )
    verdict = featherless_review.review_candidate(candidate)
    audit_log.log_event(
        "featherless_verdict", veto=verdict.veto, confidence=verdict.confidence, rationale=verdict.rationale, symbol=put_symbol,
    )
    if verdict.veto:
        return {"placed": False, "reason": f"Featherless veto: {verdict.rationale}", "gate": "featherless_veto"}

    audit_log.log_event("order_submitted", intent="open", symbol=put_symbol, qty=put_qty, side="buy", limit_price=put_limit_price)
    try:
        put_result = execution.submit_order(put_symbol, put_qty, "buy", limit_price=put_limit_price)
    except execution.AlpacaCliError as e:
        audit_log.log_event("order_result", symbol=put_symbol, status="error", error=str(e))
        return {"placed": False, "reason": f"Alpaca CLI error: {e}", "gate": "execution_error"}

    audit_log.log_event(
        "order_submitted", intent="open", symbol=underlying_symbol, qty=hedge_shares, side="buy", limit_price=hedge_limit_price,
    )
    try:
        hedge_result = execution.submit_order(underlying_symbol, hedge_shares, "buy", limit_price=hedge_limit_price)
    except execution.AlpacaCliError as e:
        # Put already filled; the hedge didn't. Don't hide it -- a distinct
        # event type so this is greppable in the audit trail, not buried as
        # just another "order_result" error.
        audit_log.log_event("hedge_leg_failed", symbol=put_symbol, hedge_symbol=underlying_symbol, error=str(e))
        position_store.record_new_position(put_symbol, put_limit_price, put_qty)
        return {
            "placed": True,
            "put_order": put_result,
            "hedge_order": None,
            "warning": f"put filled but hedge leg failed -- position is temporarily UNHEDGED: {e}",
        }

    position_store.record_new_position(put_symbol, put_limit_price, put_qty)
    hedge_store.record(
        hedge_store.HedgePosition(
            put_symbol=put_symbol, underlying_symbol=underlying_symbol, put_qty=put_qty,
            hedge_shares=hedge_shares, entry_realized_vol=realized_vol, entry_implied_vol=implied_vol,
        )
    )
    audit_log.log_event("order_result", symbol=put_symbol, status="submitted", order_id=put_result.get("id"))
    return {"placed": True, "put_order": put_result, "hedge_order": hedge_result}


@tool
def check_vol_edge_exit_actions() -> list[dict]:
    """Check every tracked Company C position for a required close: either
    the vol edge has reverted (implied vol is no longer cheap vs. current
    realized vol) or a hard 7-day-to-expiry cutoff has hit -- mirrors the
    hard DTE override in position_manager.py's exit ladder. Returns an empty
    list if nothing needs action right now. Each action carries current
    prices for both legs (same pattern as check_exit_actions) so the caller
    doesn't have to re-fetch quotes just to build the close order.
    """
    tracked = hedge_store.load_all()
    live_by_symbol = {p["symbol"]: p for p in execution.list_positions() if "symbol" in p}
    actions = []
    today = date.today()
    for put_symbol, hedge in tracked.items():
        _root, expiry, _option_type, strike = options_selector.parse_occ_symbol(put_symbol)
        dte = (expiry - today).days
        put_live = live_by_symbol.get(put_symbol)
        hedge_live = live_by_symbol.get(hedge.underlying_symbol)
        # Fall back to the entry price only if Alpaca's own current_price is
        # missing -- not expected in practice, but never crash a scheduled
        # decision cycle over a missing quote field.
        put_current_price = float(put_live.get("current_price")) if put_live and put_live.get("current_price") else None
        hedge_current_price = float(hedge_live.get("current_price")) if hedge_live and hedge_live.get("current_price") else None

        if dte <= 7:
            if put_current_price is None or hedge_current_price is None:
                continue  # no usable price to close at -- wait for the next cycle rather than guess
            actions.append({
                "put_symbol": put_symbol, "underlying_symbol": hedge.underlying_symbol,
                "put_qty": hedge.put_qty, "hedge_shares": hedge.hedge_shares, "reason": "dte_cutoff",
                "put_current_price": put_current_price, "hedge_current_price": hedge_current_price,
            })
            continue

        daily_df = _fetch_ohlc(hedge.underlying_symbol, "1Day", (today - timedelta(days=60)).isoformat())
        closes = daily_df["close"].tolist()
        realized_vol = vol_edge.realized_volatility(closes)
        if realized_vol is None:
            continue
        spot = closes[-1]
        chain = execution.get_option_chain(
            hedge.underlying_symbol, option_type="put",
            expiration_gte=expiry.isoformat(), expiration_lte=expiry.isoformat(),
        )
        snapshot = chain.get("snapshots", {}).get(put_symbol)
        if snapshot is None:
            continue
        quote = snapshot.get("latestQuote", {})
        bid, ask = quote.get("bp"), quote.get("ap")
        if not bid or not ask:
            continue
        years = dte / 365
        implied_vol = options_math.implied_volatility((bid + ask) / 2, spot, strike, years, "put")
        if implied_vol is None:
            continue
        edge = vol_edge.evaluate_edge(realized_vol, implied_vol)
        if not edge.cheap_enough:
            resolved_put_price = put_current_price if put_current_price is not None else (bid + ask) / 2
            resolved_hedge_price = hedge_current_price if hedge_current_price is not None else spot
            actions.append({
                "put_symbol": put_symbol, "underlying_symbol": hedge.underlying_symbol,
                "put_qty": hedge.put_qty, "hedge_shares": hedge.hedge_shares, "reason": "vol_edge_reverted",
                "put_current_price": resolved_put_price, "hedge_current_price": resolved_hedge_price,
            })
    return actions


@tool
def close_delta_neutral_position(
    put_symbol: str, put_qty: int, put_limit_price: float, hedge_shares: int, hedge_limit_price: float, rationale: str,
) -> dict:
    """Sell to close an existing Company C position: the put, then unwind its
    stock hedge. Not subject to blackout/watchlist/risk-cap checks (only the
    kill switch and held-qty checks) -- cutting risk should never be blocked
    by the same limits that guard opening new risk, same as close_or_trim_position.

    Args:
        put_symbol: the OCC contract symbol of the put to close.
        put_qty: number of put contracts to sell (must not exceed what's held).
        put_limit_price: limit price per put contract, in dollars.
        hedge_shares: number of hedge shares to sell (must not exceed what's held).
        hedge_limit_price: limit price per share.
        rationale: your reasoning -- recorded in the audit trail.
    """
    tracked = hedge_store.load_all()
    hedge = tracked.get(put_symbol)
    underlying_symbol = hedge.underlying_symbol if hedge else None
    positions = execution.list_positions()
    live_by_symbol = {p["symbol"]: p for p in positions if "symbol" in p}

    put_held = int(float(live_by_symbol[put_symbol]["qty"])) if put_symbol in live_by_symbol else 0
    put_order = guardrails.OrderRequest(
        underlying_symbol=underlying_symbol or "", option_symbol=put_symbol, qty=put_qty,
        side="sell", intent="close", limit_price=put_limit_price, held_qty=put_held, asset_class="option",
    )
    allowed, reason, gate = guardrails.pre_trade_check(
        put_order, account_equity_usd=0.0, open_positions_risk_usd=0.0, open_positions_count=0,
        daily_new_entries_count=0, daily_pnl_pct=0.0, watchlist=set(),
    )
    audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=put_symbol)
    if not allowed:
        return {"placed": False, "reason": reason, "gate": gate}

    audit_log.log_event("order_submitted", intent="close", symbol=put_symbol, qty=put_qty, side="sell", limit_price=put_limit_price)
    try:
        put_result = execution.submit_order(put_symbol, put_qty, "sell", limit_price=put_limit_price)
    except execution.AlpacaCliError as e:
        audit_log.log_event("order_result", symbol=put_symbol, status="error", error=str(e))
        return {"placed": False, "reason": f"Alpaca CLI error: {e}", "gate": "execution_error"}

    hedge_result = None
    if underlying_symbol and hedge_shares > 0:
        hedge_held = int(float(live_by_symbol[underlying_symbol]["qty"])) if underlying_symbol in live_by_symbol else 0
        hedge_order = guardrails.OrderRequest(
            underlying_symbol=underlying_symbol, option_symbol=underlying_symbol, qty=hedge_shares,
            side="sell", intent="close", limit_price=hedge_limit_price, held_qty=hedge_held, asset_class="equity",
        )
        allowed, reason, gate = guardrails.pre_trade_check(
            hedge_order, account_equity_usd=0.0, open_positions_risk_usd=0.0, open_positions_count=0,
            daily_new_entries_count=0, daily_pnl_pct=0.0, watchlist=set(),
        )
        audit_log.log_event("gate_result", gate=gate, allowed=allowed, reason=reason, symbol=underlying_symbol)
        if not allowed:
            # Put already closed; hedge unwind blocked (e.g. kill switch).
            # Flag it the same way a failed hedge-open is flagged.
            audit_log.log_event("hedge_leg_failed", symbol=put_symbol, hedge_symbol=underlying_symbol, error=reason)
            position_store.update_after_exit(put_symbol, 0, Stage.CLOSED)
            return {"placed": True, "put_order": put_result, "hedge_order": None, "warning": f"put closed but hedge unwind blocked: {reason}"}

        audit_log.log_event(
            "order_submitted", intent="close", symbol=underlying_symbol, qty=hedge_shares, side="sell", limit_price=hedge_limit_price,
        )
        try:
            hedge_result = execution.submit_order(underlying_symbol, hedge_shares, "sell", limit_price=hedge_limit_price)
        except execution.AlpacaCliError as e:
            audit_log.log_event("hedge_leg_failed", symbol=put_symbol, hedge_symbol=underlying_symbol, error=str(e))
            position_store.update_after_exit(put_symbol, 0, Stage.CLOSED)
            return {"placed": True, "put_order": put_result, "hedge_order": None, "warning": f"put closed but hedge leg failed: {e}"}

    position_store.update_after_exit(put_symbol, 0, Stage.CLOSED)
    hedge_store.remove(put_symbol)
    audit_log.log_event("order_result", symbol=put_symbol, status="submitted", order_id=put_result.get("id"))
    return {"placed": True, "put_order": put_result, "hedge_order": hedge_result}


ALL_TOOLS = [
    get_account_summary,
    get_positions,
    check_blackout,
    get_option_candidates,
    get_signal,
    check_exit_actions,
    recommend_watchlist_addition,
    place_option_order,
    close_or_trim_position,
    cancel_open_order,
    get_vol_edge_signal,
    place_delta_neutral_put,
    check_vol_edge_exit_actions,
    close_delta_neutral_position,
]
