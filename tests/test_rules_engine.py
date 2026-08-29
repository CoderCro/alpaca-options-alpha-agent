import pandas as pd

from src.rules_engine import (
    check_ma_support_resistance,
    check_monthly_ma10,
    check_support_resistance,
    check_trend_alignment,
    evaluate_criteria,
)

# A 17-bar zigzag with ascending peaks (15,17,19,20) and ascending troughs
# (10,11,12) around a rising close -- a clean simultaneous HH/HL uptrend.
_UPTREND_CLOSE = [10, 12, 14, 12, 11, 13, 16, 13, 12, 14, 18, 14, 13, 15, 19, 15, 13]


def _uptrend_df() -> pd.DataFrame:
    close = _UPTREND_CLOSE
    return pd.DataFrame({"open": close, "close": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close]})


_DOWNTREND_CLOSE = [19, 17, 15, 17, 18, 16, 13, 16, 17, 15, 11, 15, 16, 14, 10, 14, 16]


def _downtrend_df() -> pd.DataFrame:
    close = _DOWNTREND_CLOSE
    return pd.DataFrame({"open": close, "close": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close]})


def _flat_df(n: int) -> pd.DataFrame:
    # high/low spread must stay inside the 0.5% touch tolerance under test --
    # +-0.5 on a price of 10 is a +-5% band, which would never register as a
    # "touch" and was silently asserting the wrong thing.
    return pd.DataFrame({"open": [10.0] * n, "close": [10.0] * n, "high": [10.02] * n, "low": [9.98] * n})


def test_support_resistance_fires_on_third_touch_setup():
    weekly = pd.DataFrame(
        {
            "open": [110, 105, 100, 105, 110],
            "close": [110, 105, 100, 105, 110],
            "high": [111, 106, 101, 106, 111],
            "low": [110, 105, 100, 105, 110],
        }
    )
    daily = pd.DataFrame({"open": [150, 100.3], "close": [150, 100.3], "high": [151, 100.8], "low": [149, 99.8]})
    met, detail = check_support_resistance(weekly, daily)
    assert met is True
    assert "touched 1x" in detail


def test_support_resistance_does_not_fire_when_price_is_elsewhere():
    weekly = pd.DataFrame(
        {
            "open": [110, 105, 100, 105, 110],
            "close": [110, 105, 100, 105, 110],
            "high": [111, 106, 101, 106, 111],
            "low": [110, 105, 100, 105, 110],
        }
    )
    daily = pd.DataFrame({"open": [150, 200], "close": [150, 200], "high": [151, 201], "low": [149, 199]})
    met, _ = check_support_resistance(weekly, daily)
    assert met is False


def test_trend_alignment_bullish_across_all_three_timeframes():
    df = _uptrend_df()
    met, detail = check_trend_alignment(df, df, df)
    assert met is True
    assert "bullish" in detail


def test_trend_alignment_fails_when_one_timeframe_disagrees():
    uptrend = _uptrend_df()
    flat = _flat_df(len(uptrend))
    met, _ = check_trend_alignment(uptrend, uptrend, flat)
    assert met is False


def test_ma_support_resistance_fires_on_flat_price_hugging_its_own_ma():
    df = _flat_df(80)
    met, detail = check_ma_support_resistance(df)
    assert met is True
    assert "MA20" in detail


def test_ma_support_resistance_does_not_fire_on_a_runaway_trend():
    n = 80
    close = [100 + 5 * i for i in range(n)]
    df = pd.DataFrame({"open": close, "close": close, "high": [c + 0.5 for c in close], "low": [c - 0.5 for c in close]})
    met, _ = check_ma_support_resistance(df)
    assert met is False


def test_monthly_ma10_bullish_when_close_above_weekly_ma():
    weekly = pd.DataFrame({"close": [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]})
    monthly_above = pd.DataFrame({"close": [115]})
    met, detail = check_monthly_ma10(monthly_above, weekly)
    assert met is True
    assert "109.00" in detail  # mean of the 10 weekly closes above


def test_monthly_ma10_not_bullish_when_close_below_weekly_ma():
    weekly = pd.DataFrame({"close": [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]})
    monthly_below = pd.DataFrame({"close": [100]})
    met, _ = check_monthly_ma10(monthly_below, weekly)
    assert met is False


def test_evaluate_criteria_qualifies_with_two_of_four():
    uptrend = _uptrend_df()
    # A single weekly_df has to satisfy both check_support_resistance (a swing
    # low at 100, touched once) and check_monthly_ma10 (>=10 bars, mean 113.5)
    # simultaneously -- evaluate_criteria passes the same weekly_df to both.
    # Swing detection reads the high/low columns, not close -- keep the offset
    # tiny so the detected swing-low level (99.95) stays within 0.5% of both
    # the nominal 100 level and daily_near_level's 100.3 below.
    weekly_close = [130, 125, 120, 115, 110, 105, 100, 105, 110, 115]
    weekly_combined = pd.DataFrame(
        {
            "open": weekly_close,
            "close": weekly_close,
            "high": [c + 0.05 for c in weekly_close],
            "low": [c - 0.05 for c in weekly_close],
        }
    )
    daily_near_level = pd.DataFrame({"open": [150, 100.3], "close": [150, 100.3], "high": [151, 100.8], "low": [149, 99.8]})
    monthly_bullish = pd.DataFrame({"close": [115]})  # 115 > weekly mean of 113.5

    result = evaluate_criteria(
        weekly_df=weekly_combined,
        daily_df=daily_near_level,
        h4_df=uptrend,
        m15_df=uptrend,
        monthly_df=monthly_bullish,
    )

    # support_resistance + monthly_ma10_bullish both fire; trend_alignment fails
    # because daily_near_level (only 2 bars) can't form 3 swings, and
    # ma_support_resistance fails because 2 daily bars can't fill MA20/50/100.
    assert result.met["support_resistance"] is True
    assert result.met["monthly_ma10_bullish"] is True
    assert result.count_met == 2
    assert result.qualifies_for_trading_list is True


def test_evaluate_criteria_direction_bullish_from_trend_alignment():
    uptrend = _uptrend_df()
    result = evaluate_criteria(
        weekly_df=uptrend, daily_df=uptrend, h4_df=uptrend, m15_df=uptrend, monthly_df=pd.DataFrame({"close": [1]})
    )
    assert result.direction == "bullish"


def test_evaluate_criteria_direction_bearish_from_trend_alignment():
    downtrend = _downtrend_df()
    result = evaluate_criteria(
        weekly_df=downtrend, daily_df=downtrend, h4_df=downtrend, m15_df=downtrend, monthly_df=pd.DataFrame({"close": [1]})
    )
    assert result.direction == "bearish"


def test_evaluate_criteria_direction_falls_back_to_bullish_from_monthly_ma10():
    # Reuses the fixture from test_evaluate_criteria_qualifies_with_two_of_four:
    # daily_df only has 2 bars, so trend_alignment can't form 3 swings (no
    # direction from _trend_direction), but monthly_ma10 fires -- direction
    # should fall back to bullish since that check has no bearish counterpart.
    weekly_close = [130, 125, 120, 115, 110, 105, 100, 105, 110, 115]
    weekly_combined = pd.DataFrame(
        {
            "open": weekly_close,
            "close": weekly_close,
            "high": [c + 0.05 for c in weekly_close],
            "low": [c - 0.05 for c in weekly_close],
        }
    )
    daily_near_level = pd.DataFrame({"open": [150, 100.3], "close": [150, 100.3], "high": [151, 100.8], "low": [149, 99.8]})
    monthly_bullish = pd.DataFrame({"close": [115]})
    uptrend = _uptrend_df()

    result = evaluate_criteria(
        weekly_df=weekly_combined, daily_df=daily_near_level, h4_df=uptrend, m15_df=uptrend, monthly_df=monthly_bullish
    )
    assert result.direction == "bullish"


def test_evaluate_criteria_direction_none_when_undetermined():
    flat = _flat_df(80)
    result = evaluate_criteria(
        weekly_df=flat, daily_df=flat, h4_df=flat, m15_df=flat, monthly_df=pd.DataFrame({"close": [5.0]})
    )
    assert result.direction is None
