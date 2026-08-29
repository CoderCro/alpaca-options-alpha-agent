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

from src import audit_log, company_config, execution, featherless_review, guardrails, options_selector, position_store, rules_engine, watchlist
from src.calendar_blackout import NY_TZ, is_trading_allowed
from src.position_manager import Stage, next_action


def _position_max_risk(position: dict) -> float:
    if "cost_basis" in position:
        return abs(float(position["cost_basis"]))
    return abs(float(position.get("qty", 0)) * float(position.get("avg_entry_price", 0)) * 100)


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
    response = execution.get_bars(symbol, start, timeframe=timeframe)
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
]
