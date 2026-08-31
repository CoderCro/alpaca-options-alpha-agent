import pytest

from src.delta_hedge import compute_hedge


def test_put_hedge_is_always_a_buy():
    # Put delta is negative, so -delta is positive -> hedge_shares > 0 -> buy.
    # This is the only path Company C actually uses.
    hedge = compute_hedge(spot=100.0, strike=100.0, years=1.0, vol=0.2, option_type="put", option_qty=1)
    assert hedge.hedge_side == "buy"
    assert hedge.hedge_shares > 0


def test_call_hedge_is_always_a_sell():
    # Exercised for math correctness only -- nothing in this codebase trades
    # calls through this path (see module docstring: guardrails blocks the
    # short sale a call hedge would require).
    hedge = compute_hedge(spot=100.0, strike=100.0, years=1.0, vol=0.2, option_type="call", option_qty=1)
    assert hedge.hedge_side == "sell"
    assert hedge.hedge_shares > 0


def test_hedge_share_count_scales_with_option_quantity():
    hedge_1 = compute_hedge(spot=100.0, strike=100.0, years=1.0, vol=0.2, option_type="put", option_qty=1)
    hedge_5 = compute_hedge(spot=100.0, strike=100.0, years=1.0, vol=0.2, option_type="put", option_qty=5)
    assert hedge_5.hedge_shares == pytest.approx(hedge_1.hedge_shares * 5, abs=1)


def test_deep_itm_put_hedges_close_to_full_delta_one_shares_per_contract():
    # Deep ITM put delta approaches -1, so the hedge approaches 100 shares/contract.
    hedge = compute_hedge(spot=50.0, strike=100.0, years=1.0, vol=0.2, option_type="put", option_qty=1)
    assert hedge.option_delta == pytest.approx(-1.0, abs=0.01)
    assert hedge.hedge_shares == pytest.approx(100, abs=2)


def test_deep_otm_put_needs_almost_no_hedge():
    hedge = compute_hedge(spot=200.0, strike=100.0, years=1.0, vol=0.2, option_type="put", option_qty=1)
    assert hedge.hedge_shares < 2
