"""Blackout-window rules: market-open proximity and Tier-1 macro news days."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 30
OPEN_BLACKOUT = timedelta(hours=2)

# Verified Tier-1 macro event dates (sources: federalreserve.gov, bls.gov).
# Extend this list as new dates are confirmed -- do not guess unverified dates.
FOMC_DATES = [
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
    date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
    date(2026, 10, 28), date(2026, 12, 9),
]
NFP_DATES = [
    date(2026, 9, 4),  # August 2026 Employment Situation report
]
T1_NEWS_DATES = sorted(set(FOMC_DATES + NFP_DATES))


def _in_open_window(dt_ny: datetime) -> bool:
    market_open = dt_ny.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    return market_open - OPEN_BLACKOUT <= dt_ny <= market_open + OPEN_BLACKOUT


def _near_t1_news(d: date) -> bool:
    return any(abs((d - news_day).days) <= 1 for news_day in T1_NEWS_DATES)


def is_trading_allowed(dt: datetime) -> tuple[bool, str]:
    """Given a timezone-aware datetime, return (allowed, reason)."""
    dt_ny = dt.astimezone(NY_TZ)
    if _near_t1_news(dt_ny.date()):
        return False, "T1 macro news blackout (day before/of/after FOMC or NFP)"
    if _in_open_window(dt_ny):
        return False, "Market-open blackout (2h before/after 9:30 ET open)"
    return True, "ok"
