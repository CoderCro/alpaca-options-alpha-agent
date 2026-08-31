"""Hard, code-level guardrails for autonomous order placement.

This is the security boundary for the LangChain trading agent -- every check
here lives in code the agent cannot bypass by prompting around it, never only
in a system-prompt instruction. `pre_trade_check` is the one chokepoint every
write tool in agent_tools.py calls first, unconditionally, before any call
reaches execution.submit_order.

Pure functions throughout, mirroring position_manager.py's style: callers
(agent_tools.py) fetch live account/position state from execution.py and
pass it in, so every check here is testable without mocking a subprocess.

Exits/trims (intent="close") only ever have to clear the kill switch and a
held-qty check -- they can never be blocked by blackout, the watchlist, or
any of the risk-increasing caps below. Trapping capital in a position that
can't be exited is worse than letting an exit through during a blackout
window or after a ticker leaves the watchlist.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src import company_config
from src.calendar_blackout import NY_TZ, is_trading_allowed

KILL_SWITCH_FILENAME = "KILL_SWITCH"

PER_TRADE_RISK_PCT = 3.0                # max risk on one new entry, as % of current equity
MAX_CONCURRENT_POSITIONS = 8
MAX_AGGREGATE_RISK_PCT = 25.0           # sum of all open positions' max-loss, as % of equity
MAX_DAILY_NEW_ENTRIES = 15
DAILY_LOSS_CIRCUIT_BREAKER_PCT = -5.0   # halt new entries once today's P&L drops below this % of start-of-day equity

ALLOWED_SIDES_FOR_OPEN = {"buy"}        # long calls/puts only -- v1 has no short/naked structures


@dataclass
class OrderRequest:
    underlying_symbol: str
    option_symbol: str
    qty: int
    side: Literal["buy", "sell"]
    intent: Literal["open", "close"]
    limit_price: float
    held_qty: int = 0  # for intent="close": how much of this exact contract is actually held
    asset_class: Literal["option", "equity"] = "option"  # equity: Company C's delta-hedge leg (see delta_hedge.py) -- no contract multiplier

    @property
    def max_risk_usd(self) -> float:
        if self.asset_class == "equity":
            return self.qty * self.limit_price
        return self.qty * self.limit_price * 100


def kill_switch_active(kill_switch_file: Path | None = None) -> tuple[bool, str]:
    kill_switch_file = kill_switch_file or company_config.state_path(KILL_SWITCH_FILENAME)
    if kill_switch_file.exists():
        reason = kill_switch_file.read_text().strip() or "no reason given"
        return True, f"kill switch engaged: {reason}"
    if os.environ.get("TRADING_HALTED") == "1":
        return True, "kill switch engaged: TRADING_HALTED=1"
    return False, "ok"


def pre_trade_check(
    order: OrderRequest,
    *,
    account_equity_usd: float,
    open_positions_risk_usd: float,
    open_positions_count: int,
    daily_new_entries_count: int,
    daily_pnl_pct: float,
    watchlist: set[str],
    now: datetime | None = None,
) -> tuple[bool, str, str]:
    """Returns (allowed, reason, gate_name). Checks short-circuit on first failure."""
    killed, kill_reason = kill_switch_active()
    if killed:
        return False, kill_reason, "kill_switch"

    if order.intent == "close":
        if order.qty > order.held_qty:
            return False, f"close qty {order.qty} exceeds held qty {order.held_qty} -- would open a naked position", "structure"
        return True, "ok", "none"

    # Everything below applies to intent == "open" only.
    allowed, blackout_reason = is_trading_allowed(now or datetime.now(NY_TZ))
    if not allowed:
        return False, blackout_reason, "blackout"

    if order.underlying_symbol not in watchlist:
        return False, f"{order.underlying_symbol} is not on the approved watchlist", "watchlist"

    if order.side not in ALLOWED_SIDES_FOR_OPEN:
        return (
            False,
            f"side={order.side!r} is not a defined-risk long entry (v1 supports long calls/puts only)",
            "structure",
        )

    max_risk_allowed = account_equity_usd * (PER_TRADE_RISK_PCT / 100)
    if order.max_risk_usd > max_risk_allowed:
        return (
            False,
            f"${order.max_risk_usd:,.0f} exceeds the {PER_TRADE_RISK_PCT}% per-trade cap (${max_risk_allowed:,.0f})",
            "risk_cap",
        )

    if open_positions_count >= MAX_CONCURRENT_POSITIONS:
        return False, f"{open_positions_count} open positions already at the {MAX_CONCURRENT_POSITIONS} cap", "position_count"

    max_aggregate_allowed = account_equity_usd * (MAX_AGGREGATE_RISK_PCT / 100)
    if open_positions_risk_usd + order.max_risk_usd > max_aggregate_allowed:
        return (
            False,
            f"adding ${order.max_risk_usd:,.0f} would exceed the {MAX_AGGREGATE_RISK_PCT}% aggregate-risk cap (${max_aggregate_allowed:,.0f})",
            "aggregate_risk",
        )

    if daily_new_entries_count >= MAX_DAILY_NEW_ENTRIES:
        return (
            False,
            f"{daily_new_entries_count} new entries already placed today, at the {MAX_DAILY_NEW_ENTRIES}/day cap",
            "daily_order_count",
        )

    if daily_pnl_pct <= DAILY_LOSS_CIRCUIT_BREAKER_PCT:
        return (
            False,
            f"today's P&L ({daily_pnl_pct:.1f}%) has hit the {DAILY_LOSS_CIRCUIT_BREAKER_PCT}% daily-loss circuit breaker -- new entries halted",
            "daily_loss_breaker",
        )

    return True, "ok", "none"
