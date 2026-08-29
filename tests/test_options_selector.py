from datetime import date

from src.options_selector import select_option_candidates

AS_OF = date(2026, 8, 30)
UNDERLYING_PRICE = 200.0


def _snapshot(bid=4.50, ask=4.70, delta=0.42):
    return {"latestQuote": {"bp": bid, "ap": ask}, "greeks": {"delta": delta}}


def test_filters_to_requested_direction():
    snapshots = {
        "TEST260925C00200000": _snapshot(),  # call, in every other window -- should survive
        "TEST260925P00200000": _snapshot(),  # put -- excluded when direction=bullish
    }
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert [c.symbol for c in result] == ["TEST260925C00200000"]


def test_bearish_direction_selects_puts():
    snapshots = {
        "TEST260925C00200000": _snapshot(),
        "TEST260925P00200000": _snapshot(),
    }
    result = select_option_candidates(snapshots, "bearish", UNDERLYING_PRICE, as_of=AS_OF)
    assert [c.symbol for c in result] == ["TEST260925P00200000"]


def test_excludes_expiry_too_soon():
    # 2026-09-05 is 6 days out from AS_OF -- below the default 21-day floor
    snapshots = {"TEST260905C00200000": _snapshot()}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert result == []


def test_excludes_expiry_too_far():
    # 2026-11-01 is 63 days out -- above the default 45-day ceiling
    snapshots = {"TEST261101C00200000": _snapshot()}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert result == []


def test_includes_expiry_within_window():
    # 2026-09-25 is 26 days out -- inside (21, 45)
    snapshots = {"TEST260925C00200000": _snapshot()}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert len(result) == 1
    assert result[0].dte == 26


def test_excludes_strike_too_far_otm():
    # strike 260 vs underlying 200 -> moneyness 1.3, outside the default (0.95, 1.05)
    snapshots = {"TEST260925C00260000": _snapshot()}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert result == []


def test_zero_delta_maps_to_none():
    # Live-observed feed behavior: greeks.delta == 0 means "not computed", not a real value.
    snapshots = {"TEST260925C00200000": _snapshot(delta=0)}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert result[0].delta is None


def test_nonzero_delta_is_preserved():
    snapshots = {"TEST260925C00200000": _snapshot(delta=0.42)}
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert result[0].delta == 0.42


def test_sorted_by_proximity_to_at_the_money():
    snapshots = {
        "TEST260925C00210000": _snapshot(),  # moneyness 1.05
        "TEST260925C00200000": _snapshot(),  # moneyness 1.00 -- closest to ATM
        "TEST260925C00195000": _snapshot(),  # moneyness 0.975
    }
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF)
    assert [c.strike for c in result] == [200.0, 195.0, 210.0]


def test_truncates_to_max_candidates():
    snapshots = {
        f"TEST260925C002{strike:02d}000": _snapshot()
        for strike in range(0, 10)  # ten distinct near-the-money strikes: 200, 201, ... 209
    }
    result = select_option_candidates(snapshots, "bullish", UNDERLYING_PRICE, as_of=AS_OF, max_candidates=5)
    assert len(result) == 5


def test_invalid_direction_raises():
    import pytest

    with pytest.raises(ValueError, match="bullish"):
        select_option_candidates({}, "sideways", UNDERLYING_PRICE, as_of=AS_OF)
