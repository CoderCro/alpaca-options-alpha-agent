"""Realized-vs-implied volatility edge: Company C's entry signal.

Realized vol comes from the underlying's own price history via
execution.get_bars -- unaffected by the OPRA block that stops a historical
*options* backtest (see backtest_signal.py), since this only needs stock/ETF
bars. Implied vol is options_math.implied_volatility solved against the live
chain's quoted price, never read off the chain's own greeks/IV fields --
those come back zeroed/absent (live-verified against SPY, 2026-08-30).

guardrails.ALLOWED_SIDES_FOR_OPEN only permits "buy", so the only side of
this edge that's actually executable is "implied vol is cheap relative to
trailing realized vol" (buy underpriced convexity). Harvesting the other
side (implied rich vs. realized -- the more common case, i.e. the usual
variance risk premium) would need short/naked or spread structures this
system deliberately doesn't allow. Treating trailing realized vol as the
forecast is itself a simplification (no GARCH/EWMA decay, no vol-of-vol
adjustment) -- flagged, not yet contested, same spirit as rules_engine.py's
numeric choices.

Zero real trades through Sep 1: live-checked, the actual edge on SPY was
-0.0297 (implied *richer* than realized), which is the structurally common
case -- the market usually prices vol at a premium to what subsequently
realizes, so "implied is cheap by 3+ points" is the rarer regime, not the
default one. Not a bug; a threshold calibrated for patience the hackathon's
remaining window doesn't afford. MIN_EDGE_THRESHOLD lowered accordingly
(user's call) -- this trades on a weaker, noisier edge than 3 points would,
closer to normal bid/ask and estimation noise. Real tradeoff, not free.
DTE window narrowed from 21-45 to 1-3 for the same reason (also user's
call) -- more expiries checked per cycle, more chances to clear the bar
before the deadline.
"""

import math
from dataclasses import dataclass

TRADING_DAYS_PER_YEAR = 252
MIN_EDGE_THRESHOLD = 0.01  # lowered from 0.03 -- see module docstring, this is a real quality/frequency tradeoff
MIN_DTE = 1  # narrowed from 21 -- avoids years=0 (see options_math.py: BS collapses to intrinsic-only at years<=0, IV unsolvable)
MAX_DTE = 3  # narrowed from 45
EXIT_DTE_FLOOR = 0  # close once truly expiring (dte<=0), not <=7 -- with MIN_DTE=1, a <=7 floor would fire on literally the next cycle after every entry, before the position ever gets a chance

# The default ATM-centered moneyness band (shared with A/B's own candidate
# selection) picks a put with delta ~0.5-0.6 at this DTE -- live-calibrated
# 2026-09-02 against real DIS/NVDA/WMT chains: delta is extremely sensitive
# to strike this close to expiry, swinging from ~0.9 to ~0.02 across just a
# few percent of moneyness. This band landed around delta 0.25-0.40 for
# those three names -- materially smaller hedge notional than ATM -- without
# going so far OTM the quote gets too thin to solve an IV from (deeper
# strikes were down to a few cents, often unsolvable).
MONEYNESS_RANGE = (0.97, 0.995)
# Derived from guardrails.PER_TRADE_RISK_PCT_OVERRIDES["c"] (6%, i.e. $6,000
# on a $100,000 account) and the ~0.35 representative delta the band above
# landed on: 6000 / (0.35 * 100) ~= $171.43, rounded down to stay on the
# affordable side. Skips the option-chain fetch and IV solve entirely for
# underlyings that can't clear the hedge cap regardless of which candidate
# gets picked -- e.g. DIS/WMT (~$105-109) work, NVDA (~$225) structurally
# doesn't at this delta/cap.
MAX_UNDERLYING_PRICE = 170


def realized_volatility(closes: list[float], window: int = 20) -> float | None:
    """Annualized stdev of daily log returns over the trailing window.
    None when there isn't enough history yet -- never silently compute on
    too little data."""
    if len(closes) < window + 1:
        return None
    recent = closes[-(window + 1):]
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


@dataclass
class VolEdge:
    realized_vol: float
    implied_vol: float
    edge: float          # realized_vol - implied_vol; positive means implied looks cheap
    cheap_enough: bool    # edge >= threshold, i.e. worth acting on


def evaluate_edge(realized_vol: float, implied_vol: float, threshold: float = MIN_EDGE_THRESHOLD) -> VolEdge:
    edge = realized_vol - implied_vol
    return VolEdge(realized_vol=realized_vol, implied_vol=implied_vol, edge=edge, cheap_enough=edge >= threshold)
