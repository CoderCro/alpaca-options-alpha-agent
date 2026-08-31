from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.scheduler import run_all_companies, within_market_hours

NY = ZoneInfo("America/New_York")


def test_within_market_hours_at_open():
    assert within_market_hours(datetime(2026, 8, 31, 9, 30, tzinfo=NY)) is True  # Monday


def test_within_market_hours_at_close():
    assert within_market_hours(datetime(2026, 8, 31, 16, 0, tzinfo=NY)) is True


def test_within_market_hours_before_open():
    assert within_market_hours(datetime(2026, 8, 31, 9, 29, tzinfo=NY)) is False


def test_within_market_hours_after_close():
    assert within_market_hours(datetime(2026, 8, 31, 16, 1, tzinfo=NY)) is False


def test_within_market_hours_on_saturday():
    assert within_market_hours(datetime(2026, 9, 5, 12, 0, tzinfo=NY)) is False  # Saturday


def test_within_market_hours_on_sunday():
    assert within_market_hours(datetime(2026, 8, 30, 12, 0, tzinfo=NY)) is False  # Sunday


def test_run_all_companies_invokes_each_script_as_a_subprocess():
    with patch("src.scheduler.subprocess.run", return_value=type("R", (), {"returncode": 0, "stdout": "3 tickers evaluated", "stderr": ""})()) as mock_run:
        run_all_companies()

    called_modules = [call.args[0][-1] for call in mock_run.call_args_list]
    assert called_modules == ["src.run_company_a", "src.run_company_b", "src.run_company_c"]


def test_run_all_companies_does_not_raise_when_a_company_fails():
    with patch("src.scheduler.subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": "", "stderr": "traceback..."})()):
        run_all_companies()  # should not raise
