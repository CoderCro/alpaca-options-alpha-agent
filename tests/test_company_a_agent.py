"""Company A's orchestration logic (mechanical direction/qty/candidate
derivation) is tested in isolation from agent_tools' own internals --
guardrails/veto/execution wiring is already covered by test_agent_tools.py
-- by mocking directly at the .func level of each tool it calls through.
"""

from unittest.mock import patch

from src import company_a_agent


def _signal(qualifies=True, direction="bullish", met=None):
    return {
        "qualifies_for_trading_list": qualifies,
        "direction": direction,
        "met": met or {},
        "details": {},
        "count_met": 2,
    }


def _candidate(symbol="AAPL270115C00200000", ask=4.0):
    return {
        "symbol": symbol,
        "expiry": "2027-01-15",
        "strike": 200.0,
        "dte": 30,
        "moneyness": 1.0,
        "bid": 3.8,
        "ask": ask,
        "delta": 0.4,
    }


def test_no_exits_and_no_qualifying_tickers_produces_no_signal_action():
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_signal.func", return_value=_signal(qualifies=False)),
    ):
        result = company_a_agent.run_trading_cycle(["AAPL"])

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "no_signal"


def test_direction_none_is_skipped_not_guessed():
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_signal.func", return_value=_signal(qualifies=True, direction=None)),
        patch("src.agent_tools.place_option_order.func") as mock_place,
    ):
        result = company_a_agent.run_trading_cycle(["AAPL"])

    mock_place.assert_not_called()
    assert result["actions"][0]["action"] == "no_signal"


def test_empty_candidates_is_skipped():
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_signal.func", return_value=_signal()),
        patch("src.agent_tools.get_option_candidates.func", return_value=[]),
        patch("src.agent_tools.place_option_order.func") as mock_place,
    ):
        result = company_a_agent.run_trading_cycle(["AAPL"])

    mock_place.assert_not_called()
    assert result["actions"][0]["action"] == "no_candidates"


def test_entry_picks_first_candidate_and_sizes_by_risk_cap():
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_signal.func", return_value=_signal(met={"trend_alignment": True})),
        patch(
            "src.agent_tools.get_option_candidates.func",
            return_value=[_candidate(ask=5.0), _candidate(symbol="OTHER", ask=6.0)],
        ),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch(
            "src.agent_tools.place_option_order.func", return_value={"placed": True, "order": {"id": "o1"}}
        ) as mock_place,
    ):
        result = company_a_agent.run_trading_cycle(["AAPL"])

    # 3% of 100,000 = 3,000 max risk; ask 5.0 * 100 = 500/contract -> floor(3000/500) = 6
    mock_place.assert_called_once_with(
        underlying_symbol="AAPL",
        option_symbol="AAPL270115C00200000",
        qty=6,
        limit_price=5.0,
        direction="bullish",
        rationale="mechanical 2-of-4 gate: {'trend_alignment': True}",
    )
    assert result["actions"][0]["action"] == "entry_attempt"


def test_qty_floors_to_minimum_one_contract():
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[]),
        patch("src.agent_tools.get_signal.func", return_value=_signal()),
        patch("src.agent_tools.get_option_candidates.func", return_value=[_candidate(ask=5000.0)]),
        patch("src.agent_tools.get_account_summary.func", return_value={"equity": 100_000.0}),
        patch("src.agent_tools.place_option_order.func", return_value={"placed": False, "reason": "risk cap"}) as mock_place,
    ):
        company_a_agent.run_trading_cycle(["AAPL"])

    assert mock_place.call_args.kwargs["qty"] == 1


def test_exit_actions_processed_before_entries():
    exit_action = {
        "symbol": "AAPL261016C00210000",
        "sell_qty": 2,
        "reason": "+20% profit",
        "next_stage": "TRANCHE_1_DONE",
        "current_price": 2.40,
    }
    with (
        patch("src.agent_tools.check_exit_actions.func", return_value=[exit_action]),
        patch("src.agent_tools.close_or_trim_position.func", return_value={"placed": True}) as mock_close,
        patch("src.agent_tools.get_signal.func", return_value=_signal(qualifies=False)),
    ):
        result = company_a_agent.run_trading_cycle(["AAPL"])

    mock_close.assert_called_once_with(
        option_symbol="AAPL261016C00210000",
        qty=2,
        limit_price=2.40,
        rationale="+20% profit",
        next_stage="TRANCHE_1_DONE",
    )
    assert result["actions"][0]["action"] == "exit"
