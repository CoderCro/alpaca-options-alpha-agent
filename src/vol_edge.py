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
"""

import math
from dataclasses import dataclass

TRADING_DAYS_PER_YEAR = 252
MIN_EDGE_THRESHOLD = 0.03  # implied vol must be >=3 vol points cheap vs. realized to act on -- flagged, not yet contested


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
