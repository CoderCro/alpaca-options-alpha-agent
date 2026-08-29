from src.watchlist import Watchlist, load, recommend_addition, save


def test_load_missing_file_returns_empty(tmp_path):
    watchlist = load(tmp_path / "missing.json")
    assert watchlist.approved == set()
    assert watchlist.pending == set()


def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "watchlist.json"
    save(Watchlist(approved={"AAPL", "SPY"}, pending={"NVDA"}), path)
    loaded = load(path)
    assert loaded.approved == {"AAPL", "SPY"}
    assert loaded.pending == {"NVDA"}


def test_recommend_addition_only_touches_pending(tmp_path):
    path = tmp_path / "watchlist.json"
    save(Watchlist(approved={"AAPL"}), path)
    result = recommend_addition("NVDA", path)
    assert result.pending == {"NVDA"}
    assert result.approved == {"AAPL"}


def test_recommend_addition_is_noop_if_already_approved(tmp_path):
    path = tmp_path / "watchlist.json"
    save(Watchlist(approved={"AAPL"}), path)
    result = recommend_addition("AAPL", path)
    assert result.pending == set()
