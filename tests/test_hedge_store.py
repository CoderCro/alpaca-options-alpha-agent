from src.hedge_store import HedgePosition, load_all, record, remove


def _position(**overrides) -> HedgePosition:
    defaults = dict(
        put_symbol="SPY261016P00650000",
        underlying_symbol="SPY",
        put_qty=1,
        hedge_shares=42,
        entry_realized_vol=0.22,
        entry_implied_vol=0.17,
    )
    defaults.update(overrides)
    return HedgePosition(**defaults)


def test_load_missing_file_returns_empty(tmp_path):
    assert load_all(tmp_path / "missing.json") == {}


def test_record_then_load_roundtrips(tmp_path):
    path = tmp_path / "state.json"
    record(_position(), path)
    positions = load_all(path)
    pos = positions["SPY261016P00650000"]
    assert pos.underlying_symbol == "SPY"
    assert pos.hedge_shares == 42
    assert pos.entry_realized_vol == 0.22
    assert pos.entry_implied_vol == 0.17


def test_remove_deletes_the_entry(tmp_path):
    path = tmp_path / "state.json"
    record(_position(), path)
    remove("SPY261016P00650000", path)
    assert load_all(path) == {}


def test_remove_is_noop_for_unknown_symbol(tmp_path):
    path = tmp_path / "state.json"
    record(_position(), path)
    remove("UNKNOWN", path)  # should not raise
    assert "SPY261016P00650000" in load_all(path)
