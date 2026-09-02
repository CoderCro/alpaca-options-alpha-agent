from src.position_manager import Position, Stage, next_action


def _position(**overrides) -> Position:
    defaults = dict(entry_price=2.00, original_qty=10, remaining_qty=10, stage=Stage.OPEN)
    defaults.update(overrides)
    return Position(**defaults)


def test_tranche_1_at_20_percent():
    pos = _position()
    action = next_action(pos, current_price=2.40, days_to_expiry=30)
    assert action.sell_qty == 2  # 20% of 10
    assert action.next_stage == Stage.TRANCHE_1_DONE


def test_no_action_below_tranche_1_threshold():
    pos = _position()
    assert next_action(pos, current_price=2.10, days_to_expiry=30) is None


def test_open_stage_closes_on_50_percent_loss():
    # A position that never reaches +20% first had no stop-loss at all
    # before this -- live-confirmed real positions sitting at -75% with
    # nothing acting on them, since TAIL_STOP_LOSS_PCT only ever applies
    # after TRANCHE_2_DONE.
    pos = _position()
    action = next_action(pos, current_price=1.00, days_to_expiry=30)  # -50%
    assert action.sell_qty == 10  # entire remaining position, not a tranche
    assert action.next_stage == Stage.CLOSED


def test_open_stage_no_action_above_stop_loss_threshold():
    pos = _position()
    assert next_action(pos, current_price=1.30, days_to_expiry=30) is None  # -35%, worse than nothing but not -50%


def test_tranche_2_at_45_percent():
    pos = _position(remaining_qty=8, stage=Stage.TRANCHE_1_DONE)
    action = next_action(pos, current_price=2.90, days_to_expiry=30)
    assert action.sell_qty == 2  # 20% of original 10
    assert action.next_stage == Stage.TRANCHE_2_DONE


def test_breakeven_stop_triggers_tail_via_stop():
    pos = _position(remaining_qty=6, stage=Stage.TRANCHE_2_DONE)
    action = next_action(pos, current_price=2.00, days_to_expiry=30)  # back to entry price = breakeven
    assert action.sell_qty == 3  # 50% of remaining 6
    assert action.next_stage == Stage.TAIL_VIA_STOP


def test_95_percent_before_stop_triggers_tail_via_target():
    pos = _position(remaining_qty=6, stage=Stage.TRANCHE_2_DONE)
    action = next_action(pos, current_price=3.90, days_to_expiry=30)  # +95%
    assert action.sell_qty == 3
    assert action.next_stage == Stage.TAIL_VIA_TARGET


def test_tail_via_stop_closes_on_20_percent_loss():
    pos = _position(remaining_qty=3, stage=Stage.TAIL_VIA_STOP)
    action = next_action(pos, current_price=1.60, days_to_expiry=30)  # -20%
    assert action.sell_qty == 3
    assert action.next_stage == Stage.CLOSED


def test_tail_via_stop_closes_on_100_percent_profit():
    pos = _position(remaining_qty=3, stage=Stage.TAIL_VIA_STOP)
    action = next_action(pos, current_price=4.00, days_to_expiry=30)  # +100%
    assert action.sell_qty == 3
    assert action.next_stage == Stage.CLOSED


def test_tail_via_target_defers_to_featherless():
    pos = _position(remaining_qty=3, stage=Stage.TAIL_VIA_TARGET)
    assert next_action(pos, current_price=5.00, days_to_expiry=30) is None


def test_dte_floor_overrides_every_stage():
    for stage in Stage:
        if stage == Stage.CLOSED:
            continue
        pos = _position(remaining_qty=4, stage=stage)
        action = next_action(pos, current_price=2.00, days_to_expiry=7)
        assert action.sell_qty == 4
        assert action.next_stage == Stage.CLOSED


def test_closed_position_takes_no_further_action():
    pos = _position(remaining_qty=0, stage=Stage.CLOSED)
    assert next_action(pos, current_price=999, days_to_expiry=30) is None
