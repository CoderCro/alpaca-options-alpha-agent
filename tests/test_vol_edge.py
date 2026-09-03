import math

import pytest

from src.vol_edge import (
    MAX_DTE,
    MAX_UNDERLYING_PRICE,
    MIN_DTE,
    MIN_EDGE_THRESHOLD,
    MONEYNESS_RANGE,
    evaluate_edge,
    realized_volatility,
)


def test_dte_window_is_short_dated_and_excludes_zero():
    # MIN_DTE > 0 is load-bearing, not a style choice: at years=0 exactly,
    # options_math.bs_price collapses to intrinsic-only regardless of vol,
    # making implied_volatility unsolvable for any real quote (see
    # options_math.py). Company C must never select a same-day (0 DTE) contract.
    assert MIN_DTE >= 1
    assert MAX_DTE >= MIN_DTE


def test_moneyness_range_is_below_atm_and_ordered():
    # Below-ATM biases puts toward smaller |delta| (smaller hedge notional);
    # see the module comment for the live-calibrated derivation.
    assert MONEYNESS_RANGE[0] < MONEYNESS_RANGE[1] <= 1.0


def test_max_underlying_price_matches_its_derivation():
    # Derived from guardrails' Company C override (6%, $6,000 on $100k) and a
    # ~0.35 representative delta from MONEYNESS_RANGE -- guards that the two
    # stay consistent if either is revisited later.
    from src.guardrails import PER_TRADE_RISK_PCT_OVERRIDES

    representative_delta = 0.35
    max_risk_usd = 100_000 * PER_TRADE_RISK_PCT_OVERRIDES["c"] / 100
    assert MAX_UNDERLYING_PRICE <= max_risk_usd / (representative_delta * 100)


def test_edge_threshold_uses_the_lowered_default():
    edge = evaluate_edge(realized_vol=0.16, implied_vol=0.15)  # 1 vol point, below the old 3-point bar
    assert edge.cheap_enough is True
    assert MIN_EDGE_THRESHOLD < 0.03


def test_realized_volatility_returns_none_with_insufficient_history():
    assert realized_volatility([100.0] * 10, window=20) is None


def test_realized_volatility_is_zero_for_a_constant_price_series():
    closes = [100.0] * 25
    assert realized_volatility(closes, window=20) == pytest.approx(0.0, abs=1e-9)


def test_realized_volatility_matches_hand_computed_value_for_constant_daily_return():
    # A constant per-day log return produces zero *variance* in returns, so
    # this is really another zero-vol check, from a different construction
    # (compounding growth) than the flat-price case above.
    closes = [100.0 * math.exp(0.001 * i) for i in range(25)]
    assert realized_volatility(closes, window=20) == pytest.approx(0.0, abs=1e-6)


def test_realized_volatility_is_positive_for_a_noisy_series():
    closes = [100.0, 102.0, 99.0, 103.0, 98.0, 104.0, 97.0, 105.0, 96.0, 106.0,
              95.0, 107.0, 94.0, 108.0, 93.0, 109.0, 92.0, 110.0, 91.0, 111.0, 90.0]
    vol = realized_volatility(closes, window=20)
    assert vol is not None
    assert vol > 0


def test_evaluate_edge_flags_cheap_implied_vol_as_actionable():
    edge = evaluate_edge(realized_vol=0.30, implied_vol=0.20, threshold=0.03)
    assert edge.edge == pytest.approx(0.10)
    assert edge.cheap_enough is True


def test_evaluate_edge_rejects_edge_below_threshold():
    edge = evaluate_edge(realized_vol=0.21, implied_vol=0.20, threshold=0.03)
    assert edge.cheap_enough is False


def test_evaluate_edge_rejects_when_implied_is_rich_not_cheap():
    # implied > realized -- the usual variance-risk-premium direction, which
    # this system can't act on (no short/naked structures allowed).
    edge = evaluate_edge(realized_vol=0.15, implied_vol=0.25, threshold=0.03)
    assert edge.edge < 0
    assert edge.cheap_enough is False
