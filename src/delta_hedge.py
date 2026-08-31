"""Delta-neutral position construction: pairs a long option with an
offsetting stock position so combined delta is ~0, isolating P&L to the vol
edge (vega/gamma) rather than direction.

Company C only ever proposes puts (see vol_edge.py's module docstring for
why) -- a put's delta is negative, so the hedge is always a stock BUY, never
a short sale. This function is written generically (correct for calls too)
so the math is independently testable, but nothing in this codebase calls it
with option_type="call".

Delta here is always Black-Scholes-computed (options_math.bs_delta), never
read off Alpaca's chain -- see options_math.py's docstring for why.
"""

from dataclasses import dataclass
from typing import Literal

from src.options_math import bs_delta


@dataclass
class HedgeOrder:
    option_delta: float
    option_qty: int
    hedge_shares: int  # always >=0 -- see hedge_side for direction
    hedge_side: Literal["buy", "sell"]


def compute_hedge(
    spot: float,
    strike: float,
    years: float,
    vol: float,
    option_type: Literal["call", "put"],
    option_qty: int,
) -> HedgeOrder:
    delta = bs_delta(spot, strike, years, vol, option_type)
    # Each contract controls 100 shares of directional exposure; the hedge
    # holds the opposite-signed share count to bring net delta to ~0.
    raw_shares = -delta * option_qty * 100
    hedge_shares = round(raw_shares)
    side: Literal["buy", "sell"] = "buy" if hedge_shares >= 0 else "sell"
    return HedgeOrder(option_delta=delta, option_qty=option_qty, hedge_shares=abs(hedge_shares), hedge_side=side)
