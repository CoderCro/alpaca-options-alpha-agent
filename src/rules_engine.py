"""The 2-of-4 entry-criteria engine: combines support/resistance, multi-timeframe
trend, MA-as-S&R, and monthly-vs-weekly-MA10 into a single trading-list decision.

Each check_* function takes OHLC DataFrames (columns: open/high/low/close) and
returns (met: bool, detail: str) -- detail is a human-readable trace of why,
meant to flow into the dashboard/write-up alongside Featherless's own rationale.
"""

from dataclasses import dataclass

import pandas as pd

from src.indicators import count_touches, find_swing_highs, find_swing_lows, is_near, simple_moving_average

TOUCH_TOLERANCE_PCT = 0.5
SR_LOOKBACK_WEEKS = 26
MA_WINDOWS = (20, 50, 100)
MA_LOOKBACK_BARS = 60
MA_MIN_TOUCHES = 2
MONTHLY_MA_WINDOW = 10


def check_support_resistance(
    weekly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    tolerance_pct: float = TOUCH_TOLERANCE_PCT,
    lookback_weeks: int = SR_LOOKBACK_WEEKS,
) -> tuple[bool, str]:
    recent_weekly = weekly_df.tail(lookback_weeks)
    levels = [v for _, v in find_swing_highs(recent_weekly)] + [v for _, v in find_swing_lows(recent_weekly)]
    current_price = daily_df["close"].iloc[-1]

    for level in levels:
        touches = count_touches(recent_weekly, level, tolerance_pct)
        if touches in (1, 2) and is_near(current_price, level, tolerance_pct):
            return True, f"weekly level {level:.2f} touched {touches}x, daily price {current_price:.2f} testing for touch #{touches + 1}"
    return False, "no weekly S&R level with 1-2 prior touches is currently being retested on the daily"


def _trend_direction(df: pd.DataFrame, order: int = 2) -> str | None:
    highs = find_swing_highs(df, order)
    lows = find_swing_lows(df, order)
    if len(highs) < 3 or len(lows) < 3:
        return None

    last_highs = [v for _, v in highs[-3:]]
    last_lows = [v for _, v in lows[-3:]]

    if all(last_highs[i] < last_highs[i + 1] for i in range(2)) and all(last_lows[i] < last_lows[i + 1] for i in range(2)):
        return "bullish"
    if all(last_highs[i] > last_highs[i + 1] for i in range(2)) and all(last_lows[i] > last_lows[i + 1] for i in range(2)):
        return "bearish"
    return None


def check_trend_alignment(daily_df: pd.DataFrame, h4_df: pd.DataFrame, m15_df: pd.DataFrame) -> tuple[bool, str]:
    directions = {
        "daily": _trend_direction(daily_df),
        "4h": _trend_direction(h4_df),
        "15m": _trend_direction(m15_df),
    }
    aligned = directions["daily"] is not None and directions["daily"] == directions["4h"] == directions["15m"]
    if aligned:
        return True, f"{directions['daily']} HH/HL aligned across daily, 4h, 15m"
    return False, f"not aligned: daily={directions['daily']}, 4h={directions['4h']}, 15m={directions['15m']}"


def check_ma_support_resistance(
    daily_df: pd.DataFrame,
    tolerance_pct: float = TOUCH_TOLERANCE_PCT,
    lookback_bars: int = MA_LOOKBACK_BARS,
    min_touches: int = MA_MIN_TOUCHES,
) -> tuple[bool, str]:
    recent = daily_df.tail(lookback_bars)
    for window in MA_WINDOWS:
        ma = simple_moving_average(daily_df["close"], window).reindex(recent.index)
        touches = sum(
            1
            for idx in recent.index
            if pd.notna(ma.loc[idx])
            and (is_near(recent.loc[idx, "low"], ma.loc[idx], tolerance_pct) or is_near(recent.loc[idx, "high"], ma.loc[idx], tolerance_pct))
        )
        if touches >= min_touches:
            return True, f"MA{window} acted as S&R with {touches} touches in the last {lookback_bars} bars"
    return False, f"no MA{MA_WINDOWS} showing >={min_touches} touches as S&R in the last {lookback_bars} bars"


def check_monthly_ma10(monthly_df: pd.DataFrame, weekly_df: pd.DataFrame, ma_window: int = MONTHLY_MA_WINDOW) -> tuple[bool, str]:
    weekly_ma = simple_moving_average(weekly_df["close"], ma_window)
    if weekly_ma.isna().all():
        return False, f"fewer than {ma_window} weekly bars available"

    latest_weekly_ma = weekly_ma.iloc[-1]
    latest_monthly_close = monthly_df["close"].iloc[-1]
    met = bool(latest_monthly_close > latest_weekly_ma)
    return met, f"monthly close {latest_monthly_close:.2f} vs weekly-MA{ma_window} {latest_weekly_ma:.2f}"


@dataclass
class CriteriaResult:
    met: dict[str, bool]
    details: dict[str, str]

    @property
    def count_met(self) -> int:
        return sum(self.met.values())

    @property
    def qualifies_for_trading_list(self) -> bool:
        return self.count_met >= 2


def evaluate_criteria(
    *,
    weekly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    h4_df: pd.DataFrame,
    m15_df: pd.DataFrame,
    monthly_df: pd.DataFrame,
) -> CriteriaResult:
    sr_met, sr_detail = check_support_resistance(weekly_df, daily_df)
    trend_met, trend_detail = check_trend_alignment(daily_df, h4_df, m15_df)
    ma_met, ma_detail = check_ma_support_resistance(daily_df)
    monthly_met, monthly_detail = check_monthly_ma10(monthly_df, weekly_df)

    return CriteriaResult(
        met={
            "support_resistance": sr_met,
            "trend_alignment": trend_met,
            "ma_support_resistance": ma_met,
            "monthly_ma10_bullish": monthly_met,
        },
        details={
            "support_resistance": sr_detail,
            "trend_alignment": trend_detail,
            "ma_support_resistance": ma_detail,
            "monthly_ma10_bullish": monthly_detail,
        },
    )
