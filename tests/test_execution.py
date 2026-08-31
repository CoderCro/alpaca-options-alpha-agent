"""subprocess.run is mocked throughout -- these tests must never invoke the
real `alpaca` binary or hit the live network/API (paper account or not).
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.execution import (
    AlpacaCliError,
    cancel_order,
    get_account,
    get_bars,
    get_crypto_bars,
    get_option_chain,
    get_order,
    get_portfolio_history,
    list_open_orders,
    list_positions,
    submit_order,
)


def _completed(payload, returncode: int = 0) -> MagicMock:
    return MagicMock(stdout=json.dumps(payload), returncode=returncode)


@patch("subprocess.run")
def test_get_account_parses_json(mock_run):
    mock_run.return_value = _completed({"id": "abc-123", "equity": "100000"})
    assert get_account() == {"id": "abc-123", "equity": "100000"}
    mock_run.assert_called_once_with(["alpaca", "account", "get"], capture_output=True, text=True, timeout=30)


@patch("subprocess.run")
def test_list_positions_parses_json_array(mock_run):
    mock_run.return_value = _completed([{"symbol": "AAPL261016C00210000"}])
    assert list_positions() == [{"symbol": "AAPL261016C00210000"}]


@patch("subprocess.run")
def test_list_positions_empty(mock_run):
    mock_run.return_value = _completed([])
    assert list_positions() == []


@patch("subprocess.run")
def test_get_option_chain_only_required_arg(mock_run):
    mock_run.return_value = _completed({"snapshots": {}})
    get_option_chain("AAPL")
    args = mock_run.call_args[0][0]
    assert args == ["alpaca", "data", "option", "chain", "--underlying-symbol", "AAPL"]


@patch("subprocess.run")
def test_get_option_chain_all_filters(mock_run):
    mock_run.return_value = _completed({"snapshots": {}})
    get_option_chain(
        "AAPL",
        option_type="call",
        expiration_gte="2026-10-01",
        expiration_lte="2026-11-01",
        strike_gte=190.0,
        strike_lte=210.0,
    )
    args = mock_run.call_args[0][0]
    assert args == [
        "alpaca", "data", "option", "chain",
        "--underlying-symbol", "AAPL",
        "--type", "call",
        "--expiration-date-gte", "2026-10-01",
        "--expiration-date-lte", "2026-11-01",
        "--strike-price-gte", "190.0",
        "--strike-price-lte", "210.0",
    ]


@patch("subprocess.run")
def test_submit_order_builds_expected_args(mock_run):
    mock_run.return_value = _completed({"id": "order-1", "status": "accepted"})
    order = submit_order("AAPL261016C00210000", 1, "buy", limit_price=4.50)
    assert order["id"] == "order-1"
    args = mock_run.call_args[0][0]
    assert args == [
        "alpaca", "order", "submit",
        "--symbol", "AAPL261016C00210000",
        "--qty", "1",
        "--side", "buy",
        "--type", "limit",
        "--time-in-force", "day",
        "--limit-price", "4.5",
    ]


@patch("subprocess.run")
def test_get_order(mock_run):
    mock_run.return_value = _completed({"id": "order-123", "status": "filled"})
    assert get_order("order-123")["status"] == "filled"
    mock_run.assert_called_once_with(
        ["alpaca", "order", "get", "--order-id", "order-123"], capture_output=True, text=True, timeout=30
    )


@patch("subprocess.run")
def test_list_open_orders(mock_run):
    mock_run.return_value = _completed([{"id": "o1"}, {"id": "o2"}])
    assert len(list_open_orders()) == 2
    mock_run.assert_called_once_with(
        ["alpaca", "order", "list", "--status", "open"], capture_output=True, text=True, timeout=30
    )


@patch("subprocess.run")
def test_cancel_order_treats_empty_json_as_success(mock_run):
    mock_run.return_value = _completed({})
    assert cancel_order("order-123") is None
    mock_run.assert_called_once_with(
        ["alpaca", "order", "cancel", "--order-id", "order-123"], capture_output=True, text=True, timeout=30
    )


@patch("subprocess.run")
def test_nonzero_exit_raises_alpaca_cli_error_with_message(mock_run):
    mock_run.return_value = _completed(
        {"code": 0, "error": "insufficient buying power", "hint": "reduce qty", "status": 0}, returncode=1
    )
    with pytest.raises(AlpacaCliError, match="insufficient buying power"):
        get_account()


@patch("subprocess.run")
def test_cancel_order_nonzero_exit_raises(mock_run):
    mock_run.return_value = _completed(
        {"code": 0, "error": "order not found", "hint": "", "status": 0}, returncode=1
    )
    with pytest.raises(AlpacaCliError, match="order not found"):
        cancel_order("bad-id")


@patch("subprocess.run")
def test_cli_timeout_raises_alpaca_cli_error(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["alpaca"], timeout=30)
    with pytest.raises(AlpacaCliError, match="timed out"):
        get_account()


@patch("subprocess.run")
def test_malformed_json_output_raises_alpaca_cli_error(mock_run):
    mock_run.return_value = MagicMock(stdout="not json", returncode=0)
    with pytest.raises(AlpacaCliError, match="non-JSON"):
        get_account()


@patch("subprocess.run")
def test_get_bars_only_required_args(mock_run):
    mock_run.return_value = _completed({"bars": []})
    get_bars("AAPL", "2026-08-01")
    args = mock_run.call_args[0][0]
    assert args == ["alpaca", "data", "bars", "--symbol", "AAPL", "--start", "2026-08-01", "--timeframe", "1Day"]


@patch("subprocess.run")
def test_get_bars_all_options(mock_run):
    mock_run.return_value = _completed({"bars": []})
    get_bars("AAPL", "2026-08-01", end="2026-08-30", timeframe="15Min", limit=100, feed="iex")
    args = mock_run.call_args[0][0]
    assert args == [
        "alpaca", "data", "bars",
        "--symbol", "AAPL",
        "--start", "2026-08-01",
        "--timeframe", "15Min",
        "--end", "2026-08-30",
        "--limit", "100",
        "--feed", "iex",
    ]


@patch("subprocess.run")
def test_get_crypto_bars_only_required_args(mock_run):
    mock_run.return_value = _completed({"bars": {}})
    get_crypto_bars("BTC/USD", "2026-08-01")
    args = mock_run.call_args[0][0]
    assert args == ["alpaca", "data", "crypto", "bars", "--symbols", "BTC/USD", "--start", "2026-08-01", "--timeframe", "1Day"]


@patch("subprocess.run")
def test_get_option_chain_follows_pagination(mock_run):
    mock_run.side_effect = [
        _completed({"snapshots": {"AAPL261016C00210000": {"bid": 1}}, "next_page_token": "abc"}),
        _completed({"snapshots": {"AAPL261016P00210000": {"bid": 2}}}),
    ]
    result = get_option_chain("AAPL")
    assert result["snapshots"] == {
        "AAPL261016C00210000": {"bid": 1},
        "AAPL261016P00210000": {"bid": 2},
    }
    assert mock_run.call_count == 2
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "--page-token" in second_call_args
    assert second_call_args[second_call_args.index("--page-token") + 1] == "abc"


@patch("subprocess.run")
def test_get_portfolio_history_default_args(mock_run):
    mock_run.return_value = _completed({"timestamp": [1], "equity": [100000.0]})
    result = get_portfolio_history()
    assert result["equity"] == [100000.0]
    mock_run.assert_called_once_with(
        ["alpaca", "account", "portfolio", "--period", "1W", "--timeframe", "1H"],
        capture_output=True, text=True, timeout=30,
    )


@patch("subprocess.run")
def test_get_portfolio_history_custom_args(mock_run):
    mock_run.return_value = _completed({"timestamp": [], "equity": []})
    get_portfolio_history(period="1M", timeframe="1D")
    args = mock_run.call_args[0][0]
    assert args == ["alpaca", "account", "portfolio", "--period", "1M", "--timeframe", "1D"]


@patch("subprocess.run")
def test_get_bars_follows_pagination(mock_run):
    mock_run.side_effect = [
        _completed({"bars": [{"c": 1}], "next_page_token": "abc"}),
        _completed({"bars": [{"c": 2}]}),
    ]
    result = get_bars("AAPL", "2026-08-01")
    assert result["bars"] == [{"c": 1}, {"c": 2}]
    assert mock_run.call_count == 2
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "--page-token" in second_call_args
    assert second_call_args[second_call_args.index("--page-token") + 1] == "abc"


@patch("subprocess.run")
def test_get_crypto_bars_follows_pagination_keyed_by_symbol(mock_run):
    mock_run.side_effect = [
        _completed({"bars": {"BTC/USD": [{"c": 1}]}, "next_page_token": "xyz"}),
        _completed({"bars": {"BTC/USD": [{"c": 2}]}}),
    ]
    result = get_crypto_bars("BTC/USD", "2026-08-01")
    assert result["bars"] == {"BTC/USD": [{"c": 1}, {"c": 2}]}
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_get_crypto_bars_all_options(mock_run):
    mock_run.return_value = _completed({"bars": {}})
    get_crypto_bars("ETH/USD", "2026-08-01", end="2026-08-30", timeframe="1Week", limit=50)
    args = mock_run.call_args[0][0]
    assert args == [
        "alpaca", "data", "crypto", "bars",
        "--symbols", "ETH/USD",
        "--start", "2026-08-01",
        "--timeframe", "1Week",
        "--end", "2026-08-30",
        "--limit", "50",
    ]
