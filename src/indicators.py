"""Reusable technical-analysis primitives: swing points, moving averages, touch detection.

Functions take a pandas DataFrame with open/high/low/close columns, indexed by
bar sequence -- nothing here depends on actual timestamps, only on bar order,
so the same functions work whether df holds daily, weekly, or monthly bars.
"""

import pandas as pd

DEFAULT_TOUCH_TOLERANCE_PCT = 0.5
DEFAULT_FRACTAL_ORDER = 2  # bars required on each side for a swing high/low


def find_swing_highs(df: pd.DataFrame, order: int = DEFAULT_FRACTAL_ORDER) -> list[tuple[int, float]]:
    highs = df["high"]
    swings = []
    for i in range(order, len(highs) - order):
        window = highs.iloc[i - order : i + order + 1]
        if highs.iloc[i] == window.max() and (window == window.max()).sum() == 1:
            swings.append((i, float(highs.iloc[i])))
    return swings


def find_swing_lows(df: pd.DataFrame, order: int = DEFAULT_FRACTAL_ORDER) -> list[tuple[int, float]]:
    lows = df["low"]
    swings = []
    for i in range(order, len(lows) - order):
        window = lows.iloc[i - order : i + order + 1]
        if lows.iloc[i] == window.min() and (window == window.min()).sum() == 1:
            swings.append((i, float(lows.iloc[i])))
    return swings


def simple_moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def is_near(price: float, level: float, tolerance_pct: float = DEFAULT_TOUCH_TOLERANCE_PCT) -> bool:
    # bool(...) avoids handing back numpy.bool_ when price/level are pandas
    # scalars -- numpy.bool_(True) is True evaluates to False (identity, not
    # equality), which silently breaks callers doing `is True` or JSON dumps.
    return bool(abs(price - level) / level * 100 <= tolerance_pct)


def count_touches(df: pd.DataFrame, level: float, tolerance_pct: float = DEFAULT_TOUCH_TOLERANCE_PCT) -> int:
    touched = df.apply(
        lambda row: is_near(row["low"], level, tolerance_pct) or is_near(row["high"], level, tolerance_pct),
        axis=1,
    )
    return int(touched.sum())
