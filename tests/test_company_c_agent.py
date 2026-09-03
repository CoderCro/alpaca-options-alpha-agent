"""Company C's orchestration logic (mechanical sizing/hedging derivation) is
tested in isolation from agent_tools' own internals -- guardrails/veto/
execution wiring is already covered by test_agent_tools.py, and the BS math
is already covered by test_options_math.py/test_delta_hedge.py -- by mocking
directly at the .func level of each tool it calls through, same approach as
test_company_a_agent.py.
"""

from unittest.mock import patch

from src import company_c_agent
from src.delta_hedge import HedgeOrder


def _candidate(symbol="SPY261016P00500000", strike=500.0, ask=5.0, bid=4.8, dte=30):
    return {"symbol": symbol, "strike": strike, "dte": dte, "bid": bid, "ask": ask}


def _signal(has_signal=True, edge=0.05, realized_vol=0.25, implied_vol=0.20, spot=500.0, years=30 / 365, candidate=None):
    return {
        "has_signal": has_signal,
        "realized_vol": realized_vol,
        "implied_vol": implied_vol,
        "edge": edge,
        "spot": spot,
        "years_to_expiry": years,
        "candidate": candidate or _candidate(),
    }


def _hedge_with_delta(delta: float):
    """delta doesn't depend on qty -- real bs_delta() behaves the same way,
    which is exactly what run_trading_cycle's sizing now relies on (probes
    delta once at qty=1, then sizes for real)."""

    def side_effect(**kwargs):
        qty = kwargs["option_qty"]
        return HedgeOrder(option_delta=delta, option_qty=qty, hedge_shares=round(abs(delta) * qty * 100), hedge_side="buy")

    return side_effect


def test_no_exits_and_no_signal_produces_no_signal_action():
    with (
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal(has_signal=False)),
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "no_signal"


def test_entry_sizes_by_the_more_restrictive_of_premium_or_hedge_cap():
    # Company C's own override: 6% of 100,000 = 6,000 max risk (see
    # guardrails.PER_TRADE_RISK_PCT_OVERRIDES). Premium bound: ask 5.0*100 =
    # 500/contract -> 12. Hedge bound: |delta|*100*spot = 0.1*100*50 =
    # 500/contract -> 12. Chosen so both bounds agree, isolating that the
    # dual-bound sizing doesn't change the answer when the two coincide.
    with (
        patch("src.guardrails.company_config.get_company", return_value="c"),
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal(spot=50.0)),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.delta_hedge.compute_hedge", side_effect=_hedge_with_delta(-0.1)) as mock_hedge,
        patch(
            "src.agent_tools.place_delta_neutral_put.func",
            return_value={"placed": True, "put_order": {"id": "o1"}, "hedge_order": {"id": "o2"}},
        ) as mock_place,
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    assert mock_hedge.call_count == 2  # delta probe (qty=1) + real sizing
    assert mock_hedge.call_args_list[-1].kwargs["option_qty"] == 12
    mock_place.assert_called_once_with(
        underlying_symbol="SPY",
        put_symbol="SPY261016P00500000",
        put_qty=12,
        put_limit_price=5.0,
        hedge_shares=120,  # round(0.1 * 12 * 100)
        hedge_limit_price=50.0,
        realized_vol=0.25,
        implied_vol=0.20,
        rationale="vol edge 0.0500 (realized 0.2500 vs implied 0.2000)",
    )
    assert result["actions"][0]["action"] == "entry_attempt"


def test_hedge_leg_can_be_the_binding_constraint_not_just_premium():
    # Premium bound: floor(6000/(5.0*100)) = 12. Hedge bound: floor(6000/(0.1*100*100)) = 6.
    # The hedge bound is tighter here -- exactly the case the old, premium-only
    # sizing missed: it would have sized 12 contracts and only found out the
    # hedge was too big when guardrails rejected that leg downstream.
    with (
        patch("src.guardrails.company_config.get_company", return_value="c"),
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch(
            "src.agent_tools.get_vol_edge_signal.func",
            return_value=_signal(spot=100.0, candidate=_candidate(strike=100.0, ask=5.0)),
        ),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.delta_hedge.compute_hedge", side_effect=_hedge_with_delta(-0.1)) as mock_hedge,
        patch("src.agent_tools.place_delta_neutral_put.func", return_value={"placed": True}) as mock_place,
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    assert mock_hedge.call_args_list[-1].kwargs["option_qty"] == 6
    assert mock_place.call_args.kwargs["put_qty"] == 6
    assert mock_place.call_args.kwargs["hedge_shares"] == 60  # round(0.1 * 6 * 100)
    assert result["actions"][0]["action"] == "entry_attempt"


def test_skips_as_hedge_unaffordable_when_even_one_contract_exceeds_the_cap():
    # The actual DIS numbers (2026-09-02, ATM strike -- pre-dates the OTM
    # moneyness bias): hedge bound still floors to 0 even at Company C's
    # raised 6% cap ($6,185 required for 1 contract vs. a $6,000 cap).
    # Forcing qty=1 anyway (the old behavior) would just get rejected
    # downstream by guardrails' own risk_cap gate -- skip cleanly instead of
    # attempting a trade that's already known to be oversized.
    with (
        patch("src.guardrails.company_config.get_company", return_value="c"),
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch(
            "src.agent_tools.get_vol_edge_signal.func",
            return_value=_signal(spot=108.645, candidate=_candidate(strike=109.0, ask=1.03, bid=0.77)),
        ),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.delta_hedge.compute_hedge", side_effect=_hedge_with_delta(-0.5693)),
        patch("src.agent_tools.place_delta_neutral_put.func") as mock_place,
    ):
        result = company_c_agent.run_trading_cycle(["DIS"])

    mock_place.assert_not_called()
    assert result["actions"][0]["action"] == "hedge_unaffordable"


def test_exit_actions_processed_before_entries():
    exit_action = {
        "put_symbol": "SPY261016P00500000",
        "underlying_symbol": "SPY",
        "put_qty": 6,
        "hedge_shares": 240,
        "reason": "vol_edge_reverted",
        "put_current_price": 3.20,
        "hedge_current_price": 495.0,
    }
    with (
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[exit_action]),
        patch("src.agent_tools.close_delta_neutral_position.func", return_value={"placed": True}) as mock_close,
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal(has_signal=False)),
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    mock_close.assert_called_once_with(
        put_symbol="SPY261016P00500000",
        put_qty=6,
        put_limit_price=3.20,
        hedge_shares=240,
        hedge_limit_price=495.0,
        rationale="vol-edge exit: vol_edge_reverted",
    )
    assert result["actions"][0]["action"] == "exit"
