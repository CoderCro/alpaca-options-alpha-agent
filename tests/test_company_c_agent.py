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


def test_no_exits_and_no_signal_produces_no_signal_action():
    with (
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal(has_signal=False)),
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "no_signal"


def test_entry_sizes_by_risk_cap_and_computes_hedge():
    fake_hedge = HedgeOrder(option_delta=-0.4, option_qty=6, hedge_shares=240, hedge_side="buy")
    with (
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal()),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.delta_hedge.compute_hedge", return_value=fake_hedge) as mock_hedge,
        patch(
            "src.agent_tools.place_delta_neutral_put.func",
            return_value={"placed": True, "put_order": {"id": "o1"}, "hedge_order": {"id": "o2"}},
        ) as mock_place,
    ):
        result = company_c_agent.run_trading_cycle(["SPY"])

    # 3% of 100,000 = 3,000 max risk; ask 5.0 * 100 = 500/contract -> floor(3000/500) = 6
    mock_hedge.assert_called_once_with(spot=500.0, strike=500.0, years=30 / 365, vol=0.20, option_type="put", option_qty=6)
    mock_place.assert_called_once_with(
        underlying_symbol="SPY",
        put_symbol="SPY261016P00500000",
        put_qty=6,
        put_limit_price=5.0,
        hedge_shares=240,
        hedge_limit_price=500.0,
        realized_vol=0.25,
        implied_vol=0.20,
        rationale="vol edge 0.0500 (realized 0.2500 vs implied 0.2000)",
    )
    assert result["actions"][0]["action"] == "entry_attempt"


def test_qty_floors_to_minimum_one_contract():
    fake_hedge = HedgeOrder(option_delta=-0.4, option_qty=1, hedge_shares=40, hedge_side="buy")
    with (
        patch("src.agent_tools.check_vol_edge_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_signal(candidate=_candidate(ask=5000.0))),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.delta_hedge.compute_hedge", return_value=fake_hedge),
        patch("src.agent_tools.place_delta_neutral_put.func", return_value={"placed": False, "reason": "risk cap"}) as mock_place,
    ):
        company_c_agent.run_trading_cycle(["SPY"])

    assert mock_place.call_args.kwargs["put_qty"] == 1


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
