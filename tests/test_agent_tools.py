"""Tests the tool-wrapper layer's wiring and gate enforcement -- NOT
guardrails.pre_trade_check's internal decision logic, which is already
covered exhaustively in test_guardrails.py. Here we mock pre_trade_check's
return value directly and assert the wrapper respects it (in particular,
that execution.submit_order is never reached when a gate refuses).
"""

from datetime import date, timedelta
from unittest.mock import patch

from src import agent_tools
from src.featherless_review import TradeVerdict
from src.position_manager import Position, Stage
from src.watchlist import Watchlist

APPROVED_VERDICT = TradeVerdict(veto=False, confidence=0.8, rationale="looks fine")
VETO_VERDICT = TradeVerdict(veto=True, confidence=0.9, rationale="too concentrated")


def _account(equity="100000", last_equity="100000"):
    return {"equity": equity, "last_equity": last_equity, "buying_power": "400000", "options_trading_level": 3}


def _order_kwargs(**overrides):
    defaults = dict(
        underlying_symbol="AAPL",
        option_symbol="AAPL261016C00210000",
        qty=1,
        limit_price=4.50,
        direction="bullish",
        rationale="two of four criteria met, clean setup",
    )
    defaults.update(overrides)
    return defaults


def test_place_option_order_blocked_by_gate_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"AAPL"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(False, "blackout window", "blackout")),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.place_option_order.invoke(_order_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "blackout"
    mock_submit.assert_not_called()


def test_place_option_order_blocked_by_featherless_veto_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"AAPL"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=VETO_VERDICT),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.place_option_order.invoke(_order_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "featherless_veto"
    mock_submit.assert_not_called()


def test_place_option_order_happy_path_calls_submit_and_records_position():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"AAPL"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=APPROVED_VERDICT),
        patch("src.agent_tools.execution.submit_order", return_value={"id": "order-1", "status": "accepted"}) as mock_submit,
        patch("src.agent_tools.position_store.record_new_position") as mock_record,
    ):
        result = agent_tools.place_option_order.invoke(_order_kwargs())

    assert result["placed"] is True
    assert result["order"]["id"] == "order-1"
    mock_submit.assert_called_once_with("AAPL261016C00210000", 1, "buy", limit_price=4.50)
    mock_record.assert_called_once_with("AAPL261016C00210000", 4.50, 1)


def test_place_option_order_execution_error_does_not_raise():
    from src.execution import AlpacaCliError

    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"AAPL"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=APPROVED_VERDICT),
        patch("src.agent_tools.execution.submit_order", side_effect=AlpacaCliError("insufficient buying power")),
    ):
        result = agent_tools.place_option_order.invoke(_order_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "execution_error"


def test_close_or_trim_blocked_when_qty_exceeds_held():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.execution.list_positions", return_value=[{"symbol": "AAPL261016C00210000", "qty": "1"}]),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.close_or_trim_position.invoke(
            {"option_symbol": "AAPL261016C00210000", "qty": 5, "limit_price": 5.0, "rationale": "test"}
        )

    assert result["placed"] is False
    assert result["gate"] == "structure"
    mock_submit.assert_not_called()


def test_close_or_trim_happy_path_calls_submit_with_sell_side():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.execution.list_positions", return_value=[{"symbol": "AAPL261016C00210000", "qty": "1"}]),
        patch("src.agent_tools.execution.submit_order", return_value={"id": "order-2", "status": "accepted"}) as mock_submit,
        patch("src.agent_tools.position_store.load_all", return_value={}),
    ):
        result = agent_tools.close_or_trim_position.invoke(
            {"option_symbol": "AAPL261016C00210000", "qty": 1, "limit_price": 5.0, "rationale": "hit tranche 1"}
        )

    assert result["placed"] is True
    mock_submit.assert_called_once_with("AAPL261016C00210000", 1, "sell", limit_price=5.0)


def test_close_or_trim_allowed_regardless_of_kill_switch_state_is_still_gated():
    # Sanity check that the kill switch (not mocked here, real guardrails.pre_trade_check
    # runs) still blocks a close when actually engaged -- proves close isn't ungated entirely.
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.execution.list_positions", return_value=[{"symbol": "AAPL261016C00210000", "qty": "1"}]),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
        patch("src.agent_tools.guardrails.kill_switch_active", return_value=(True, "kill switch engaged: test")),
    ):
        result = agent_tools.close_or_trim_position.invoke(
            {"option_symbol": "AAPL261016C00210000", "qty": 1, "limit_price": 5.0, "rationale": "test"}
        )

    assert result["placed"] is False
    assert result["gate"] == "kill_switch"
    mock_submit.assert_not_called()


def test_cancel_order_blocked_by_kill_switch():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.guardrails.kill_switch_active", return_value=(True, "kill switch engaged: testing")),
        patch("src.agent_tools.execution.cancel_order") as mock_cancel,
    ):
        result = agent_tools.cancel_open_order.invoke({"order_id": "order-1"})

    assert result["cancelled"] is False
    mock_cancel.assert_not_called()


def test_cancel_order_happy_path():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.guardrails.kill_switch_active", return_value=(False, "ok")),
        patch("src.agent_tools.execution.cancel_order") as mock_cancel,
    ):
        result = agent_tools.cancel_open_order.invoke({"order_id": "order-1"})

    assert result["cancelled"] is True
    mock_cancel.assert_called_once_with("order-1")


def test_get_account_summary_wraps_execution():
    with patch("src.agent_tools.execution.get_account", return_value=_account()):
        result = agent_tools.get_account_summary.invoke({})
    assert result["equity"] == 100_000.0
    assert result["options_trading_level"] == 3


def test_get_positions_wraps_execution():
    with patch("src.agent_tools.execution.list_positions", return_value=[{"symbol": "AAPL261016C00210000"}]):
        result = agent_tools.get_positions.invoke({})
    assert result == [{"symbol": "AAPL261016C00210000"}]


def test_check_blackout_returns_dict_with_allowed_and_reason():
    result = agent_tools.check_blackout.invoke({})
    assert "allowed" in result
    assert "reason" in result


def test_recommend_watchlist_addition_wraps_watchlist():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch(
            "src.agent_tools.watchlist.recommend_addition",
            return_value=Watchlist(approved={"AAPL"}, pending={"NVDA"}),
        ) as mock_recommend,
    ):
        result = agent_tools.recommend_watchlist_addition.invoke({"ticker": "NVDA", "rationale": "meets market cap floor"})

    assert result["pending"] == ["NVDA"]
    mock_recommend.assert_called_once_with("NVDA")


def test_get_option_candidates_wraps_chain_and_bars():
    # Expiry computed relative to the real date.today() so this stays valid
    # regardless of when the suite runs (get_option_candidates uses real time).
    expiry = date.today() + timedelta(days=30)
    symbol = f"AAPL{expiry.strftime('%y%m%d')}C00200000"
    with (
        patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"c": 200.0}]}),
        patch(
            "src.agent_tools.execution.get_option_chain",
            return_value={"snapshots": {symbol: {"latestQuote": {"bp": 4.0, "ap": 4.2}, "greeks": {"delta": 0.45}}}},
        ),
    ):
        result = agent_tools.get_option_candidates.invoke({"underlying_symbol": "AAPL", "direction": "bullish"})

    assert len(result) == 1
    assert result[0]["symbol"] == symbol
    assert result[0]["delta"] == 0.45


def test_get_signal_wraps_rules_engine():
    with patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"o": 100, "h": 101, "l": 99, "c": 100.5}] * 5}):
        result = agent_tools.get_signal.invoke({"ticker": "AAPL"})
    assert "met" in result
    assert "count_met" in result
    assert isinstance(result["qualifies_for_trading_list"], bool)


def test_check_exit_actions_reports_action_when_profit_threshold_hit():
    expiry = date.today() + timedelta(days=30)
    symbol = f"AAPL{expiry.strftime('%y%m%d')}C00200000"
    tracked = {symbol: Position(entry_price=2.00, original_qty=10, remaining_qty=10, stage=Stage.OPEN)}
    with (
        patch("src.agent_tools.position_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=[{"symbol": symbol, "current_price": "2.40"}]),
    ):
        result = agent_tools.check_exit_actions.invoke({})

    assert len(result) == 1
    assert result[0]["symbol"] == symbol
    assert result[0]["next_stage"] == "TRANCHE_1_DONE"


def test_check_exit_actions_empty_when_no_tracked_positions():
    with (
        patch("src.agent_tools.position_store.load_all", return_value={}),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
    ):
        result = agent_tools.check_exit_actions.invoke({})
    assert result == []
