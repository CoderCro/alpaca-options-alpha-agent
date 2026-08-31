"""Black-Scholes pricing, delta, and implied-volatility solving.

Company C's vol-edge strategy needs a trustworthy delta and implied vol to
compare against realized vol. Live-verified against the real chain (SPY,
2026-08-30) that Alpaca's snapshot returns an all-zero greeks block and no IV
field at all -- worse than the occasionally-unreliable delta options_selector.py
already flagged for Company A/B. So both are computed here from quoted prices
instead of trusted from the feed, closer to how a real market maker prices its
own theoretical value rather than the quoted one.
"""

import math
from typing import Literal

RISK_FREE_RATE = 0.04  # fixed simplification, not a live treasury-yield lookup -- flagged, not yet contested

OptionType = Literal["call", "put"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _d1_d2(spot: float, strike: float, years: float, vol: float, rate: float) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return d1, d1 - vol * math.sqrt(years)


def bs_price(spot: float, strike: float, years: float, vol: float, option_type: OptionType, rate: float = RISK_FREE_RATE) -> float:
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    discounted_strike = strike * math.exp(-rate * years)
    if option_type == "call":
        return spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
    return discounted_strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(spot: float, strike: float, years: float, vol: float, option_type: OptionType, rate: float = RISK_FREE_RATE) -> float:
    if years <= 0 or vol <= 0:
        if option_type == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1, _ = _d1_d2(spot, strike, years, vol, rate)
    return _norm_cdf(d1) if option_type == "call" else _norm_cdf(d1) - 1


def bs_vega(spot: float, strike: float, years: float, vol: float, rate: float = RISK_FREE_RATE) -> float:
    if years <= 0 or vol <= 0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, years, vol, rate)
    return spot * _norm_pdf(d1) * math.sqrt(years)


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    years: float,
    option_type: OptionType,
    rate: float = RISK_FREE_RATE,
    initial_guess: float = 0.3,
    tolerance: float = 1e-6,
    max_iterations: int = 100,
) -> float | None:
    """Newton-Raphson on Black-Scholes price; falls back to bisection when
    Newton can't make progress (vega ~0 deep ITM/OTM, or a wild iterate).
    Returns None rather than a guess when it can't converge -- fails closed,
    same philosophy as featherless_review's unparseable-output handling."""
    vol = initial_guess
    for _ in range(max_iterations):
        price = bs_price(spot, strike, years, vol, option_type, rate)
        diff = market_price - price
        if abs(diff) < tolerance:
            return vol
        vega = bs_vega(spot, strike, years, vol, rate)
        if vega < 1e-8:
            break
        vol += diff / vega
        if vol <= 0:
            break
    return _bisect_iv(market_price, spot, strike, years, option_type, rate, tolerance, max_iterations)


def _bisect_iv(
    market_price: float, spot: float, strike: float, years: float, option_type: OptionType, rate: float, tolerance: float, max_iterations: int
) -> float | None:
    low, high = 1e-4, 5.0
    price_low = bs_price(spot, strike, years, low, option_type, rate)
    price_high = bs_price(spot, strike, years, high, option_type, rate)
    if not (price_low <= market_price <= price_high):
        return None  # market price outside any achievable BS price -- bad quote or bad inputs, don't guess
    for _ in range(max_iterations):
        mid = (low + high) / 2
        price_mid = bs_price(spot, strike, years, mid, option_type, rate)
        if abs(price_mid - market_price) < tolerance:
            return mid
        if price_mid < market_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2
