import math

import pytest

from src.options_math import bs_delta, bs_price, bs_vega, implied_volatility

# S=100, K=100, T=1y, r=0.05, sigma=0.2 -- the standard textbook Black-Scholes
# example (Hull), call price = 10.4506, used as a known-value sanity check.
_TEXTBOOK = dict(spot=100.0, strike=100.0, years=1.0, vol=0.2, rate=0.05)


def test_call_price_matches_textbook_example():
    price = bs_price(**_TEXTBOOK, option_type="call")
    assert price == pytest.approx(10.4506, abs=0.01)


def test_put_price_matches_put_call_parity():
    call = bs_price(**_TEXTBOOK, option_type="call")
    put = bs_price(**_TEXTBOOK, option_type="put")
    spot, strike, years, rate = _TEXTBOOK["spot"], _TEXTBOOK["strike"], _TEXTBOOK["years"], _TEXTBOOK["rate"]
    parity_put = call - spot + strike * math.exp(-rate * years)
    assert put == pytest.approx(parity_put, abs=1e-6)


def test_call_delta_between_zero_and_one():
    delta = bs_delta(**_TEXTBOOK, option_type="call")
    assert 0.0 < delta < 1.0


def test_put_delta_between_minus_one_and_zero():
    delta = bs_delta(**_TEXTBOOK, option_type="put")
    assert -1.0 < delta < 0.0


def test_deep_itm_call_delta_approaches_one():
    delta = bs_delta(spot=200.0, strike=100.0, years=1.0, vol=0.2, option_type="call", rate=0.05)
    assert delta > 0.99


def test_deep_otm_call_delta_approaches_zero():
    delta = bs_delta(spot=50.0, strike=100.0, years=1.0, vol=0.2, option_type="call", rate=0.05)
    assert delta < 0.01


def test_expired_call_is_pure_intrinsic_value():
    assert bs_price(spot=110.0, strike=100.0, years=0.0, vol=0.2, option_type="call") == 10.0
    assert bs_price(spot=90.0, strike=100.0, years=0.0, vol=0.2, option_type="call") == 0.0


def test_implied_volatility_recovers_known_vol():
    price = bs_price(**_TEXTBOOK, option_type="call")
    recovered = implied_volatility(
        market_price=price, spot=_TEXTBOOK["spot"], strike=_TEXTBOOK["strike"],
        years=_TEXTBOOK["years"], option_type="call", rate=_TEXTBOOK["rate"],
    )
    assert recovered == pytest.approx(0.2, abs=1e-4)


def test_implied_volatility_recovers_known_vol_for_put():
    price = bs_price(**_TEXTBOOK, option_type="put")
    recovered = implied_volatility(
        market_price=price, spot=_TEXTBOOK["spot"], strike=_TEXTBOOK["strike"],
        years=_TEXTBOOK["years"], option_type="put", rate=_TEXTBOOK["rate"],
    )
    assert recovered == pytest.approx(0.2, abs=1e-4)


def test_implied_volatility_falls_back_to_bisection_from_a_bad_initial_guess():
    # A near-zero initial guess makes vega underflow to ~0 on the first
    # Newton step (d1 blows up), tripping the "can't make progress" break --
    # must fall back to bisection rather than return the bad guess unchanged.
    price = bs_price(**_TEXTBOOK, option_type="call")
    recovered = implied_volatility(
        market_price=price, spot=_TEXTBOOK["spot"], strike=_TEXTBOOK["strike"],
        years=_TEXTBOOK["years"], option_type="call", rate=_TEXTBOOK["rate"],
        initial_guess=1e-6,
    )
    assert recovered == pytest.approx(0.2, abs=1e-3)


def test_implied_volatility_is_unidentifiable_deep_itm_near_expiry_but_does_not_crash():
    # Deep ITM with days-to-expiry: price is pinned to intrinsic value across
    # a wide range of vols (vega ~0 everywhere), so the "true" vol genuinely
    # can't be recovered from price alone. The solver should still return
    # some value without crashing or hanging, not necessarily the input vol.
    price = bs_price(spot=150.0, strike=100.0, years=0.01, vol=0.2, option_type="call", rate=0.05)
    recovered = implied_volatility(
        market_price=price, spot=150.0, strike=100.0, years=0.01, option_type="call", rate=0.05,
    )
    assert recovered is not None


def test_implied_volatility_returns_none_for_price_outside_arbitrage_bounds():
    # A call can never be worth more than the spot price -- an impossible
    # quote must fail closed (None), never return a bogus vol.
    recovered = implied_volatility(market_price=999.0, spot=100.0, strike=100.0, years=1.0, option_type="call", rate=0.05)
    assert recovered is None


def test_vega_is_positive_for_a_standard_option():
    assert bs_vega(**_TEXTBOOK) > 0
