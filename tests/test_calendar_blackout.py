from datetime import datetime
from zoneinfo import ZoneInfo

from src.calendar_blackout import is_trading_allowed

NY = ZoneInfo("America/New_York")


def test_blocks_nfp_day():
    allowed, reason = is_trading_allowed(datetime(2026, 9, 4, 13, 0, tzinfo=NY))
    assert not allowed
    assert "T1" in reason


def test_blocks_day_before_nfp():
    allowed, _ = is_trading_allowed(datetime(2026, 9, 3, 13, 0, tzinfo=NY))
    assert not allowed


def test_blocks_day_after_nfp():
    allowed, _ = is_trading_allowed(datetime(2026, 9, 5, 13, 0, tzinfo=NY))
    assert not allowed


def test_blocks_market_open_window():
    allowed, reason = is_trading_allowed(datetime(2026, 8, 31, 8, 0, tzinfo=NY))
    assert not allowed
    assert "open" in reason.lower()


def test_allows_midday_normal_trading():
    allowed, reason = is_trading_allowed(datetime(2026, 8, 31, 13, 0, tzinfo=NY))
    assert allowed
    assert reason == "ok"
