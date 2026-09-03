"""Tests the tool-wrapper layer's wiring and gate enforcement -- NOT
guardrails.pre_trade_check's internal decision logic, which is already
covered exhaustively in test_guardrails.py. Here we mock pre_trade_check's
return value directly and assert the wrapper respects it (in particular,
that execution.submit_order is never reached when a gate refuses).
"""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src import agent_tools, vol_edge
from src.featherless_review import TradeVerdict
from src.hedge_store import HedgePosition
from src.options_selector import OptionCandidate
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


_VALID_SHORTLIST = [{"symbol": "AAPL261016C00210000", "expiry": "2026-10-16", "strike": 210.0, "dte": 30}]


def test_place_option_order_rejects_symbol_not_in_shortlist_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_option_candidates.func", return_value=_VALID_SHORTLIST),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
        patch("src.agent_tools.execution.get_account") as mock_get_account,
    ):
        result = agent_tools.place_option_order.invoke(_order_kwargs(option_symbol="SPY230922C650"))

    assert result["placed"] is False
    assert result["gate"] == "not_in_shortlist"
    mock_submit.assert_not_called()
    mock_get_account.assert_not_called()  # rejected before any further processing


def test_place_option_order_blocked_by_gate_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_option_candidates.func", return_value=_VALID_SHORTLIST),
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
        patch("src.agent_tools.get_option_candidates.func", return_value=_VALID_SHORTLIST),
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
        patch("src.agent_tools.get_option_candidates.func", return_value=_VALID_SHORTLIST),
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
        patch("src.agent_tools.get_option_candidates.func", return_value=_VALID_SHORTLIST),
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


# ------------------------------------------------- Company C: vol-edge tools


def _delta_neutral_kwargs(**overrides):
    defaults = dict(
        underlying_symbol="SPY",
        put_symbol="SPY261016P00500000",
        put_qty=6,
        put_limit_price=5.0,
        hedge_shares=240,
        hedge_limit_price=500.0,
        realized_vol=0.25,
        implied_vol=0.20,
        rationale="vol edge 0.05",
    )
    defaults.update(overrides)
    return defaults


def test_get_vol_edge_signal_no_signal_when_insufficient_history():
    with patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"o": 1, "h": 1, "l": 1, "c": 100.0}] * 5}):
        result = agent_tools.get_vol_edge_signal.invoke({"underlying_symbol": "SPY"})
    assert result["has_signal"] is False
    assert "reason" in result


def test_get_vol_edge_signal_no_signal_when_no_candidates():
    bars = [{"o": 100, "h": 101, "l": 99, "c": 100.0 + i * 0.1} for i in range(25)]
    with (
        patch("src.agent_tools.execution.get_bars", return_value={"bars": bars}),
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": {}}),
    ):
        result = agent_tools.get_vol_edge_signal.invoke({"underlying_symbol": "SPY"})
    assert result["has_signal"] is False
    assert "no put candidates" in result["reason"]


def test_get_vol_edge_signal_happy_path_computes_edge():
    bars = [{"o": 100, "h": 101, "l": 99, "c": 100.0}] * 25
    candidate = OptionCandidate(
        symbol="SPY261016P00100000", expiry=date.today() + timedelta(days=30), strike=100.0,
        option_type="P", dte=30, moneyness=1.0, bid=4.8, ask=5.0, delta=-0.4,
    )
    with (
        patch("src.agent_tools.execution.get_bars", return_value={"bars": bars}),
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": {}}),
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.25),
        patch("src.agent_tools.options_selector.select_option_candidates", return_value=[candidate]),
        patch("src.agent_tools.options_math.implied_volatility", return_value=0.18),
    ):
        result = agent_tools.get_vol_edge_signal.invoke({"underlying_symbol": "SPY"})

    assert result["has_signal"] is True
    assert result["realized_vol"] == 0.25
    assert result["implied_vol"] == 0.18
    assert result["edge"] == pytest.approx(0.07)
    assert result["candidate"]["symbol"] == "SPY261016P00100000"


def test_get_vol_edge_signal_no_signal_when_underlying_too_expensive_to_hedge():
    # Regression guard for the hedge-affordability pre-filter: must short-
    # circuit before the option-chain fetch (that's the point -- skip the
    # expensive lookup for names that can't clear the hedge cap regardless
    # of which candidate would've been picked), not just before placing a
    # trade.
    bars = [{"o": 500, "h": 501, "l": 499, "c": 500.0}] * 25
    with (
        patch("src.agent_tools.execution.get_bars", return_value={"bars": bars}),
        patch("src.agent_tools.execution.get_option_chain") as mock_chain,
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.25),
    ):
        result = agent_tools.get_vol_edge_signal.invoke({"underlying_symbol": "SPY"})

    assert result["has_signal"] is False
    assert "exceeds" in result["reason"]
    mock_chain.assert_not_called()


def test_get_vol_edge_signal_uses_the_short_dte_window_and_otm_moneyness():
    # Regression guard for the DTE narrowing (21-45 -> 1-3) and the OTM
    # moneyness bias: both must be passed explicitly, not left to
    # select_option_candidates' own ATM-centered default (still 21-45 /
    # 0.95-1.05, shared with Company A/B's get_option_candidates) -- getting
    # either wrong would silently put Company C back on the old window/delta.
    bars = [{"o": 100, "h": 101, "l": 99, "c": 100.0}] * 25
    with (
        patch("src.agent_tools.execution.get_bars", return_value={"bars": bars}),
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": {}}) as mock_chain,
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.25),
        patch("src.agent_tools.options_selector.select_option_candidates", return_value=[]) as mock_select,
    ):
        agent_tools.get_vol_edge_signal.invoke({"underlying_symbol": "SPY"})

    assert mock_select.call_args.kwargs["dte_range"] == (vol_edge.MIN_DTE, vol_edge.MAX_DTE)
    assert mock_select.call_args.kwargs["moneyness_range"] == vol_edge.MONEYNESS_RANGE
    chain_kwargs = mock_chain.call_args.kwargs
    days_requested = (date.fromisoformat(chain_kwargs["expiration_lte"]) - date.fromisoformat(chain_kwargs["expiration_gte"])).days
    assert days_requested == vol_edge.MAX_DTE - vol_edge.MIN_DTE


_VALID_PUT_SIGNAL = {"candidate": {"symbol": "SPY261016P00500000"}}


def test_place_delta_neutral_put_rejects_symbol_not_matching_current_signal():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
        patch("src.agent_tools.execution.get_account") as mock_get_account,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs(put_symbol="SPY230922P650"))

    assert result["placed"] is False
    assert result["gate"] == "not_in_shortlist"
    mock_submit.assert_not_called()
    mock_get_account.assert_not_called()


def test_place_delta_neutral_put_blocked_by_put_leg_gate_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"SPY"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(False, "blackout window", "blackout")),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "blackout"
    mock_submit.assert_not_called()


def test_place_delta_neutral_put_blocked_by_hedge_leg_gate_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"SPY"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch(
            "src.agent_tools.guardrails.pre_trade_check",
            side_effect=[(True, "ok", "none"), (False, "exceeds equity cap", "risk_cap")],
        ),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "risk_cap"
    mock_submit.assert_not_called()


def test_place_delta_neutral_put_blocked_by_featherless_veto_never_calls_submit():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"SPY"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=VETO_VERDICT),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs())

    assert result["placed"] is False
    assert result["gate"] == "featherless_veto"
    mock_submit.assert_not_called()


def test_place_delta_neutral_put_happy_path_submits_both_legs_and_records_state():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"SPY"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=APPROVED_VERDICT),
        patch(
            "src.agent_tools.execution.submit_order",
            side_effect=[{"id": "put-order-1"}, {"id": "hedge-order-1"}],
        ) as mock_submit,
        patch("src.agent_tools.position_store.record_new_position") as mock_record_put,
        patch("src.agent_tools.hedge_store.record") as mock_record_hedge,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs())

    assert result["placed"] is True
    assert result["put_order"]["id"] == "put-order-1"
    assert result["hedge_order"]["id"] == "hedge-order-1"
    assert mock_submit.call_args_list[0].args == ("SPY261016P00500000", 6, "buy")
    assert mock_submit.call_args_list[1].args == ("SPY", 240, "buy")
    mock_record_put.assert_called_once_with("SPY261016P00500000", 5.0, 6)
    recorded_hedge = mock_record_hedge.call_args.args[0]
    assert recorded_hedge.put_symbol == "SPY261016P00500000"
    assert recorded_hedge.hedge_shares == 240


def test_place_delta_neutral_put_hedge_leg_failure_flags_unhedged():
    from src.execution import AlpacaCliError

    with (
        patch("src.agent_tools.audit_log.log_event") as mock_audit,
        patch("src.agent_tools.get_vol_edge_signal.func", return_value=_VALID_PUT_SIGNAL),
        patch("src.agent_tools.watchlist.load", return_value=Watchlist(approved={"SPY"})),
        patch("src.agent_tools.execution.get_account", return_value=_account()),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.guardrails.pre_trade_check", return_value=(True, "ok", "none")),
        patch("src.agent_tools.featherless_review.review_candidate", return_value=APPROVED_VERDICT),
        patch(
            "src.agent_tools.execution.submit_order",
            side_effect=[{"id": "put-order-1"}, AlpacaCliError("insufficient buying power")],
        ),
        patch("src.agent_tools.position_store.record_new_position") as mock_record_put,
        patch("src.agent_tools.hedge_store.record") as mock_record_hedge,
    ):
        result = agent_tools.place_delta_neutral_put.invoke(_delta_neutral_kwargs())

    assert result["placed"] is True
    assert result["hedge_order"] is None
    assert "UNHEDGED" in result["warning"]
    mock_record_put.assert_called_once()  # the put position is real and must still be tracked
    mock_record_hedge.assert_not_called()  # no hedge actually exists
    hedge_failed_events = [c for c in mock_audit.call_args_list if c.args and c.args[0] == "hedge_leg_failed"]
    assert len(hedge_failed_events) == 1


def test_check_vol_edge_exit_actions_empty_when_no_tracked():
    with (
        patch("src.agent_tools.hedge_store.load_all", return_value={}),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
    ):
        result = agent_tools.check_vol_edge_exit_actions.invoke({})
    assert result == []


def test_check_vol_edge_exit_actions_dte_cutoff():
    expiry = date.today()  # dte=0 -- Company C enters at 1-3 DTE, so the floor is 0, not 7 (see vol_edge.EXIT_DTE_FLOOR)
    put_symbol = f"SPY{expiry.strftime('%y%m%d')}P00500000"
    tracked = {
        put_symbol: HedgePosition(
            put_symbol=put_symbol, underlying_symbol="SPY", put_qty=6, hedge_shares=240,
            entry_realized_vol=0.25, entry_implied_vol=0.20,
        )
    }
    live_positions = [
        {"symbol": put_symbol, "current_price": "3.20"},
        {"symbol": "SPY", "current_price": "495.00"},
    ]
    with (
        patch("src.agent_tools.hedge_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=live_positions),
    ):
        result = agent_tools.check_vol_edge_exit_actions.invoke({})

    assert len(result) == 1
    assert result[0]["reason"] == "dte_cutoff"
    assert result[0]["put_current_price"] == 3.20
    assert result[0]["hedge_current_price"] == 495.0


def test_check_vol_edge_exit_actions_does_not_force_close_a_fresh_1to3_dte_entry():
    # The actual regression this guards: Company C now enters at 1-3 DTE
    # (vol_edge.MIN_DTE/MAX_DTE). With the old 7-day floor, a freshly-opened
    # dte=2 position would have been flagged for "dte_cutoff" on literally
    # the very next check, before the trade ever had a chance to do anything.
    expiry = date.today() + timedelta(days=2)
    put_symbol = f"SPY{expiry.strftime('%y%m%d')}P00500000"
    tracked = {
        put_symbol: HedgePosition(
            put_symbol=put_symbol, underlying_symbol="SPY", put_qty=6, hedge_shares=240,
            entry_realized_vol=0.25, entry_implied_vol=0.20,
        )
    }
    snapshot = {put_symbol: {"latestQuote": {"bp": 4.0, "ap": 4.2}}}
    with (
        patch("src.agent_tools.hedge_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"o": 500, "h": 501, "l": 499, "c": 500.0}] * 25}),
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.30),  # edge still holds
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": snapshot}),
        patch("src.agent_tools.options_math.implied_volatility", return_value=0.20),
    ):
        result = agent_tools.check_vol_edge_exit_actions.invoke({})

    assert result == []


def test_check_vol_edge_exit_actions_reports_vol_edge_reverted():
    expiry = date.today() + timedelta(days=30)  # outside the DTE cutoff
    put_symbol = f"SPY{expiry.strftime('%y%m%d')}P00500000"
    tracked = {
        put_symbol: HedgePosition(
            put_symbol=put_symbol, underlying_symbol="SPY", put_qty=6, hedge_shares=240,
            entry_realized_vol=0.25, entry_implied_vol=0.20,
        )
    }
    live_positions = [{"symbol": put_symbol, "current_price": "3.20"}]
    snapshot = {put_symbol: {"latestQuote": {"bp": 4.0, "ap": 4.2}}}
    with (
        patch("src.agent_tools.hedge_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=live_positions),
        patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"o": 500, "h": 501, "l": 499, "c": 500.0}] * 25}),
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.15),  # now below implied -> edge reverted
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": snapshot}),
        patch("src.agent_tools.options_math.implied_volatility", return_value=0.20),
    ):
        result = agent_tools.check_vol_edge_exit_actions.invoke({})

    assert len(result) == 1
    assert result[0]["reason"] == "vol_edge_reverted"
    assert result[0]["put_current_price"] == 3.20


def test_check_vol_edge_exit_actions_no_action_when_edge_still_cheap():
    expiry = date.today() + timedelta(days=30)
    put_symbol = f"SPY{expiry.strftime('%y%m%d')}P00500000"
    tracked = {
        put_symbol: HedgePosition(
            put_symbol=put_symbol, underlying_symbol="SPY", put_qty=6, hedge_shares=240,
            entry_realized_vol=0.25, entry_implied_vol=0.20,
        )
    }
    snapshot = {put_symbol: {"latestQuote": {"bp": 4.0, "ap": 4.2}}}
    with (
        patch("src.agent_tools.hedge_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.execution.get_bars", return_value={"bars": [{"o": 500, "h": 501, "l": 499, "c": 500.0}] * 25}),
        patch("src.agent_tools.vol_edge.realized_volatility", return_value=0.30),  # still well above implied -> edge holds
        patch("src.agent_tools.execution.get_option_chain", return_value={"snapshots": snapshot}),
        patch("src.agent_tools.options_math.implied_volatility", return_value=0.20),
    ):
        result = agent_tools.check_vol_edge_exit_actions.invoke({})

    assert result == []


def test_close_delta_neutral_position_blocked_when_put_qty_exceeds_held():
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.hedge_store.load_all", return_value={}),
        patch("src.agent_tools.execution.list_positions", return_value=[]),
        patch("src.agent_tools.execution.submit_order") as mock_submit,
    ):
        result = agent_tools.close_delta_neutral_position.invoke(
            dict(put_symbol="SPY261016P00500000", put_qty=6, put_limit_price=3.0, hedge_shares=240, hedge_limit_price=495.0, rationale="exit")
        )

    assert result["placed"] is False
    assert result["gate"] == "structure"
    mock_submit.assert_not_called()


def test_close_delta_neutral_position_happy_path_closes_both_legs():
    tracked = {
        "SPY261016P00500000": HedgePosition(
            put_symbol="SPY261016P00500000", underlying_symbol="SPY", put_qty=6, hedge_shares=240,
            entry_realized_vol=0.25, entry_implied_vol=0.20,
        )
    }
    live_positions = [
        {"symbol": "SPY261016P00500000", "qty": "6"},
        {"symbol": "SPY", "qty": "240"},
    ]
    with (
        patch("src.agent_tools.audit_log.log_event"),
        patch("src.agent_tools.hedge_store.load_all", return_value=tracked),
        patch("src.agent_tools.execution.list_positions", return_value=live_positions),
        patch(
            "src.agent_tools.execution.submit_order",
            side_effect=[{"id": "put-close-1"}, {"id": "hedge-close-1"}],
        ) as mock_submit,
        patch("src.agent_tools.position_store.update_after_exit") as mock_update,
        patch("src.agent_tools.hedge_store.remove") as mock_remove,
    ):
        result = agent_tools.close_delta_neutral_position.invoke(
            dict(put_symbol="SPY261016P00500000", put_qty=6, put_limit_price=3.0, hedge_shares=240, hedge_limit_price=495.0, rationale="exit")
        )

    assert result["placed"] is True
    assert mock_submit.call_args_list[0].args == ("SPY261016P00500000", 6, "sell")
    assert mock_submit.call_args_list[1].args == ("SPY", 240, "sell")
    mock_update.assert_called_once_with("SPY261016P00500000", 0, Stage.CLOSED)
    mock_remove.assert_called_once_with("SPY261016P00500000")
