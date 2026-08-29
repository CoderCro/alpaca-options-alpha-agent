from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.guardrails import (
    DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    MAX_AGGREGATE_RISK_PCT,
    MAX_CONCURRENT_POSITIONS,
    MAX_DAILY_NEW_ENTRIES,
    PER_TRADE_RISK_PCT,
    OrderRequest,
    kill_switch_active,
    pre_trade_check,
)

NY = ZoneInfo("America/New_York")
NORMAL_TIME = datetime(2026, 8, 31, 13, 0, tzinfo=NY)  # ordinary midday, no blackout
NFP_BLACKOUT_TIME = datetime(2026, 9, 4, 13, 0, tzinfo=NY)


def _open_order(**overrides) -> OrderRequest:
    defaults = dict(
        underlying_symbol="AAPL",
        option_symbol="AAPL261016C00210000",
        qty=1,
        side="buy",
        intent="open",
        limit_price=4.50,  # $450 max risk on 1 contract
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _close_order(**overrides) -> OrderRequest:
    defaults = dict(
        underlying_symbol="AAPL",
        option_symbol="AAPL261016C00210000",
        qty=1,
        side="sell",
        intent="close",
        limit_price=5.00,
        held_qty=1,
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _check(order, **overrides):
    defaults = dict(
        account_equity_usd=100_000.0,
        open_positions_risk_usd=0.0,
        open_positions_count=0,
        daily_new_entries_count=0,
        daily_pnl_pct=0.0,
        watchlist={"AAPL", "SPY"},
        now=NORMAL_TIME,
    )
    defaults.update(overrides)
    return pre_trade_check(order, **defaults)


def test_happy_path_open_allowed():
    allowed, reason, gate = _check(_open_order())
    assert allowed is True
    assert gate == "none"


def test_happy_path_close_allowed():
    allowed, reason, gate = _check(_close_order())
    assert allowed is True


def test_kill_switch_file_blocks_open(tmp_path, monkeypatch):
    kill_file = tmp_path / "KILL_SWITCH"
    kill_file.write_text("operator investigating a bad fill")
    monkeypatch.setattr("src.guardrails.company_config.state_path", lambda filename: kill_file)
    allowed, reason, gate = _check(_open_order())
    assert allowed is False
    assert gate == "kill_switch"
    assert "bad fill" in reason


def test_kill_switch_file_blocks_close_too(tmp_path, monkeypatch):
    kill_file = tmp_path / "KILL_SWITCH"
    kill_file.write_text("halted")
    monkeypatch.setattr("src.guardrails.company_config.state_path", lambda filename: kill_file)
    allowed, _, gate = _check(_close_order())
    assert allowed is False
    assert gate == "kill_switch"


def test_kill_switch_env_var_blocks(monkeypatch):
    monkeypatch.setenv("TRADING_HALTED", "1")
    allowed, _, gate = _check(_open_order())
    assert allowed is False
    assert gate == "kill_switch"


def test_kill_switch_inactive_by_default():
    active, reason = kill_switch_active()
    assert active is False
    assert reason == "ok"


def test_blackout_blocks_open():
    allowed, reason, gate = _check(_open_order(), now=NFP_BLACKOUT_TIME)
    assert allowed is False
    assert gate == "blackout"


def test_blackout_does_not_block_close():
    allowed, _, gate = _check(_close_order(), now=NFP_BLACKOUT_TIME)
    assert allowed is True


def test_watchlist_blocks_unapproved_open():
    allowed, reason, gate = _check(_open_order(underlying_symbol="TSLA"))
    assert allowed is False
    assert gate == "watchlist"


def test_watchlist_does_not_block_close_for_delisted_ticker():
    allowed, _, gate = _check(_close_order(underlying_symbol="TSLA"), watchlist={"AAPL"})
    assert allowed is True


def test_structure_blocks_sell_as_new_open():
    allowed, reason, gate = _check(_open_order(side="sell"))
    assert allowed is False
    assert gate == "structure"


def test_close_qty_exceeding_held_qty_blocked_as_naked():
    allowed, reason, gate = _check(_close_order(qty=5, held_qty=1))
    assert allowed is False
    assert gate == "structure"
    assert "naked" in reason


def test_close_qty_equal_to_held_qty_allowed():
    allowed, _, _ = _check(_close_order(qty=1, held_qty=1))
    assert allowed is True


def test_per_trade_risk_cap_blocks_oversized_entry():
    # 100 contracts * $4.50 * 100 = $45,000 max risk, way over 3% of $100k ($3,000)
    allowed, reason, gate = _check(_open_order(qty=100))
    assert allowed is False
    assert gate == "risk_cap"


def test_per_trade_risk_cap_allows_at_the_boundary():
    # $3,000 max risk == exactly 3% of $100k equity
    allowed, _, _ = _check(_open_order(qty=1, limit_price=30.0), account_equity_usd=100_000.0)
    assert allowed is True
    assert PER_TRADE_RISK_PCT == 3.0  # guards the boundary math above if the constant ever changes


def test_max_concurrent_positions_blocks_at_cap():
    allowed, reason, gate = _check(_open_order(), open_positions_count=MAX_CONCURRENT_POSITIONS)
    assert allowed is False
    assert gate == "position_count"


def test_max_concurrent_positions_allows_below_cap():
    allowed, _, _ = _check(_open_order(), open_positions_count=MAX_CONCURRENT_POSITIONS - 1)
    assert allowed is True


def test_aggregate_risk_cap_blocks_when_exceeded():
    # already at 24.6% of equity ($24,600), adding $450 more crosses 25% ($25,000)
    allowed, reason, gate = _check(_open_order(), open_positions_risk_usd=24_600.0)
    assert allowed is False
    assert gate == "aggregate_risk"


def test_aggregate_risk_cap_allows_within_bound():
    allowed, _, _ = _check(_open_order(), open_positions_risk_usd=1_000.0)
    assert allowed is True
    assert MAX_AGGREGATE_RISK_PCT == 25.0


def test_daily_new_entry_cap_blocks_at_limit():
    allowed, reason, gate = _check(_open_order(), daily_new_entries_count=MAX_DAILY_NEW_ENTRIES)
    assert allowed is False
    assert gate == "daily_order_count"


def test_daily_loss_circuit_breaker_blocks_new_entry():
    allowed, reason, gate = _check(_open_order(), daily_pnl_pct=DAILY_LOSS_CIRCUIT_BREAKER_PCT)
    assert allowed is False
    assert gate == "daily_loss_breaker"


def test_daily_loss_circuit_breaker_does_not_block_close():
    allowed, _, _ = _check(_close_order(), daily_pnl_pct=-10.0)
    assert allowed is True


def test_gate_order_kill_switch_beats_everything(monkeypatch):
    # Even with an otherwise-invalid order (bad watchlist, blackout time), the
    # kill switch reason must be what's reported -- proves check ordering.
    monkeypatch.setenv("TRADING_HALTED", "1")
    allowed, reason, gate = _check(_open_order(underlying_symbol="TSLA"), now=NFP_BLACKOUT_TIME)
    assert allowed is False
    assert gate == "kill_switch"
