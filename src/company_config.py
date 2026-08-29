"""Process-wide "which company" setting.

Safe as simple module-level state specifically because each company runs as
its own process (see run_company_a.py / run_company_b.py) -- there is only
ever one value per process, set once at startup before any state-file path
is resolved, so there is no cross-company interference risk.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_current_company = "default"


def set_company(name: str) -> None:
    global _current_company
    _current_company = name


def get_company() -> str:
    return _current_company


def state_path(filename: str) -> Path:
    directory = REPO_ROOT / "state" / _current_company
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def log_dir() -> Path:
    return REPO_ROOT / "logs" / _current_company
