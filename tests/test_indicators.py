import pandas as pd

from src.indicators import (
    count_touches,
    find_swing_highs,
    find_swing_lows,
    is_near,
    simple_moving_average,
)


def _ohlc(values: list[float], as_high: bool) -> pd.DataFrame:
    if as_high:
        highs, lows = values, [v - 1 for v in values]
    else:
        lows, highs = values, [v + 1 for v in values]
    return pd.DataFrame({"open": highs, "high": highs, "low": lows, "close": highs})


def test_find_swing_highs_detects_local_peaks():
    df = _ohlc([1, 2, 3, 5, 3, 2, 1, 2, 4, 2, 1], as_high=True)
    swings = find_swing_highs(df, order=2)
    assert swings == [(3, 5.0), (8, 4.0)]


def test_find_swing_lows_detects_local_troughs():
    df = _ohlc([5, 4, 3, 1, 3, 4, 5, 4, 2, 4, 5], as_high=False)
    swings = find_swing_lows(df, order=2)
    assert swings == [(3, 1.0), (8, 2.0)]


def test_find_swing_highs_ignores_flat_ties():
    df = _ohlc([1, 2, 5, 5, 2, 1], as_high=True)
    assert find_swing_highs(df, order=2) == []


def test_simple_moving_average():
    sma = simple_moving_average(pd.Series([1, 2, 3, 4, 5]), window=3)
    assert sma.isna().sum() == 2
    assert sma.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_is_near_within_tolerance():
    assert is_near(100.4, 100, tolerance_pct=0.5) is True
    assert is_near(101.0, 100, tolerance_pct=0.5) is False


def test_count_touches():
    df = pd.DataFrame(
        {
            "low": [99.7, 90.0, 100.3, 80.0, 50.0],
            "high": [105.0, 95.0, 110.0, 100.4, 60.0],
        }
    )
    assert count_touches(df, level=100.0, tolerance_pct=0.5) == 3
