"""Scaled exit ladder for long options positions.

Thresholds are measured against the position's entry premium (per-contract
price), not the underlying -- this works uniformly for calls and puts since
premium rises when the position gains value either way.

This module is a pure state machine: next_action() computes what SHOULD
happen given the position's current stage and today's price/DTE. It does
not talk to Alpaca and does not mutate the position -- the caller executes
the returned action and then advances the position's stage/remaining_qty.

The days-to-expiry floor overrides every stage unconditionally. In the one
branch where Featherless has discretion (TAIL_VIA_TARGET), next_action()
returns None -- the deterministic engine has nothing more to say, and the
caller is responsible for separately consulting Featherless for *when* to
exit. That discretion still operates inside the DTE floor above: it decides
timing, never whether the position survives past expiry.
"""

from dataclasses import dataclass
from enum import Enum, auto


class Stage(Enum):
    OPEN = auto()
    TRANCHE_1_DONE = auto()   # 20% of original sold at +20% profit
    TRANCHE_2_DONE = auto()   # 40% of original sold total at +45% profit; breakeven stop now armed
    TAIL_VIA_STOP = auto()    # breakeven stop hit first -> 70% sold total; last 30% on -20%/+100%
    TAIL_VIA_TARGET = auto()  # +95% hit before stop -> 70% sold total; last 30% is Featherless's call
    CLOSED = auto()


DTE_FLOOR = 7  # conservative edge of the 5-7 day window: close as soon as DTE drops to 7

TRANCHE_1_PROFIT_PCT = 20
TRANCHE_2_PROFIT_PCT = 45
TAIL_TARGET_PROFIT_PCT = 95
TAIL_STOP_LOSS_PCT = -20
TAIL_STOP_PROFIT_PCT = 100

TRANCHE_1_FRACTION_OF_ORIGINAL = 0.20
TRANCHE_2_FRACTION_OF_ORIGINAL = 0.20
TAIL_FRACTION_OF_REMAINING = 0.50


@dataclass
class Position:
    entry_price: float
    original_qty: int
    remaining_qty: int
    stage: Stage = Stage.OPEN


@dataclass
class ExitAction:
    sell_qty: int
    reason: str
    next_stage: Stage


def _profit_pct(position: Position, current_price: float) -> float:
    # Rounded to kill binary float noise at exact thresholds (e.g. 2.40/2.00
    # landing on 19.999999999999996 instead of 20.0) -- option prices only
    # carry cent-level precision, so 6 decimal places loses nothing real.
    return round((current_price - position.entry_price) / position.entry_price * 100, 6)


def next_action(position: Position, current_price: float, days_to_expiry: int) -> ExitAction | None:
    if position.stage == Stage.CLOSED or position.remaining_qty <= 0:
        return None

    if days_to_expiry <= DTE_FLOOR:
        return ExitAction(position.remaining_qty, f"DTE floor ({days_to_expiry}d <= {DTE_FLOOR}d)", Stage.CLOSED)

    profit_pct = _profit_pct(position, current_price)

    if position.stage == Stage.OPEN and profit_pct >= TRANCHE_1_PROFIT_PCT:
        qty = round(position.original_qty * TRANCHE_1_FRACTION_OF_ORIGINAL)
        return ExitAction(qty, f"+{TRANCHE_1_PROFIT_PCT}% profit reached", Stage.TRANCHE_1_DONE)

    if position.stage == Stage.TRANCHE_1_DONE and profit_pct >= TRANCHE_2_PROFIT_PCT:
        qty = round(position.original_qty * TRANCHE_2_FRACTION_OF_ORIGINAL)
        return ExitAction(qty, f"+{TRANCHE_2_PROFIT_PCT}% profit reached, arming breakeven stop", Stage.TRANCHE_2_DONE)

    if position.stage == Stage.TRANCHE_2_DONE:
        if profit_pct <= 0:
            qty = round(position.remaining_qty * TAIL_FRACTION_OF_REMAINING)
            return ExitAction(qty, "breakeven stop triggered", Stage.TAIL_VIA_STOP)
        if profit_pct >= TAIL_TARGET_PROFIT_PCT:
            qty = round(position.remaining_qty * TAIL_FRACTION_OF_REMAINING)
            return ExitAction(qty, f"+{TAIL_TARGET_PROFIT_PCT}% profit reached before stop", Stage.TAIL_VIA_TARGET)
        return None

    if position.stage == Stage.TAIL_VIA_STOP:
        if profit_pct <= TAIL_STOP_LOSS_PCT or profit_pct >= TAIL_STOP_PROFIT_PCT:
            reason = f"tail hit {TAIL_STOP_LOSS_PCT}%/{TAIL_STOP_PROFIT_PCT}% band"
            return ExitAction(position.remaining_qty, reason, Stage.CLOSED)
        return None

    if position.stage == Stage.TAIL_VIA_TARGET:
        return None  # deferred to Featherless; see featherless_review

    return None
