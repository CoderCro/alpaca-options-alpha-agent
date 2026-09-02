import subprocess
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.scheduler import COMPANY_AB_SCRIPTS, COMPANY_C_SCRIPT, run_companies, sync_audit_logs, within_market_hours

NY = ZoneInfo("America/New_York")


def _completed(stdout: str = "") -> type:
    return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


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


def test_run_companies_invokes_each_script_as_a_subprocess():
    with patch("src.scheduler.subprocess.run", return_value=_completed(stdout="3 tickers evaluated")) as mock_run:
        run_companies(COMPANY_AB_SCRIPTS)

    called_modules = [call.args[0][-1] for call in mock_run.call_args_list]
    assert called_modules == ["src.run_company_a", "src.run_company_b"]


def test_run_companies_can_run_just_company_c():
    with patch("src.scheduler.subprocess.run", return_value=_completed()) as mock_run:
        run_companies([COMPANY_C_SCRIPT])

    called_modules = [call.args[0][-1] for call in mock_run.call_args_list]
    assert called_modules == ["src.run_company_c"]


def test_run_companies_does_not_raise_when_a_company_fails():
    with patch("src.scheduler.subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": "", "stderr": "traceback..."})()):
        run_companies(COMPANY_AB_SCRIPTS)  # should not raise


def test_sync_audit_logs_skips_cleanly_when_nothing_changed():
    with patch("src.scheduler.subprocess.run", return_value=_completed(stdout="")) as mock_run:
        sync_audit_logs()
    mock_run.assert_called_once()  # only the status check -- no add/commit/push on a clean tree


def test_sync_audit_logs_commits_and_pushes_when_logs_changed():
    with patch(
        "src.scheduler.subprocess.run",
        side_effect=[_completed(stdout="M logs/a/audit_2026-09-02.jsonl"), _completed(), _completed(), _completed()],
    ) as mock_run:
        sync_audit_logs()
    git_subcommands = [call.args[0][1] for call in mock_run.call_args_list]
    assert git_subcommands == ["status", "add", "commit", "push"]


def test_sync_audit_logs_does_not_raise_when_push_fails():
    with patch(
        "src.scheduler.subprocess.run",
        side_effect=[
            _completed(stdout="M logs/a/audit_2026-09-02.jsonl"),
            _completed(),
            _completed(),
            subprocess.CalledProcessError(1, ["git", "push"]),
        ],
    ):
        sync_audit_logs()  # should not raise -- a sync failure must never crash a trading cycle
