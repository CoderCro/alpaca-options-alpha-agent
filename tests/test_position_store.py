from src.position_manager import Stage
from src.position_store import load_all, record_new_position, save_all, update_after_exit


def test_load_missing_file_returns_empty(tmp_path):
    assert load_all(tmp_path / "missing.json") == {}


def test_record_new_position_then_load_roundtrips(tmp_path):
    path = tmp_path / "state.json"
    record_new_position("AAPL261016C00210000", 4.50, 10, path)
    positions = load_all(path)
    pos = positions["AAPL261016C00210000"]
    assert pos.entry_price == 4.50
    assert pos.original_qty == 10
    assert pos.remaining_qty == 10
    assert pos.stage == Stage.OPEN


def test_update_after_exit_changes_remaining_qty_and_stage(tmp_path):
    path = tmp_path / "state.json"
    record_new_position("AAPL261016C00210000", 4.50, 10, path)
    update_after_exit("AAPL261016C00210000", 8, Stage.TRANCHE_1_DONE, path)
    positions = load_all(path)
    pos = positions["AAPL261016C00210000"]
    assert pos.remaining_qty == 8
    assert pos.stage == Stage.TRANCHE_1_DONE


def test_update_after_exit_is_noop_for_unknown_symbol(tmp_path):
    path = tmp_path / "state.json"
    save_all({}, path)
    update_after_exit("UNKNOWN", 1, Stage.CLOSED, path)  # should not raise
    assert load_all(path) == {}
