"""Runs all three companies' decision cycles periodically during market hours.

Started once per trading day (`python3 -m src.scheduler`) -- replaces
manually re-invoking run_company_a/b/c throughout the day. Exits at market
close rather than persisting across days: only a few trading days remain
before the hackathon deadline, and a fresh start each morning is simpler and
safer than the restart-on-crash/reboot-survival concerns a real multi-day
daemon would need.

Shells out to each company's own entry-point script as a subprocess rather
than importing and calling them in-process. Each script already loads its
own company-specific .env correctly (see run_company_b.py/run_company_c.py);
running all three in one process would mean juggling os.environ overrides
between calls -- a real way to leak one company's Alpaca keys into another's
CLI call. Subprocess isolation sidesteps that entirely, and matches how
execution.py already shells out to the Alpaca CLI for the same reason.

Blackout logic (market-open window, T1 news days) is NOT reimplemented here
-- guardrails.pre_trade_check already enforces it on every order this loop
triggers. This only decides *when to check*, never whether a trade is allowed.
"""

import subprocess
import sys
import time
from datetime import datetime
from datetime import time as dtime

from src.calendar_blackout import NY_TZ

CHECK_INTERVAL_SECONDS = 15 * 60
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
COMPANY_SCRIPTS = ["src.run_company_a", "src.run_company_b", "src.run_company_c"]


def within_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def run_all_companies() -> None:
    for module in COMPANY_SCRIPTS:
        result = subprocess.run([sys.executable, "-m", module], capture_output=True, text=True)
        status = "ok" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        output = (result.stdout or result.stderr).strip()
        print(f"[{datetime.now(NY_TZ):%H:%M:%S}] {module} [{status}]: {output}")


def main() -> None:
    print(
        f"Scheduler started {datetime.now(NY_TZ):%Y-%m-%d %H:%M:%S %Z} -- "
        f"checking every {CHECK_INTERVAL_SECONDS // 60} min, exits at market close."
    )
    while True:
        now = datetime.now(NY_TZ)
        if now.weekday() >= 5:
            print(f"[{now:%H:%M:%S}] Weekend, no trading. Exiting.")
            return
        if now.time() > MARKET_CLOSE:
            print(f"[{now:%H:%M:%S}] Market closed for today. Exiting -- start again tomorrow.")
            return
        if now.time() < MARKET_OPEN:
            print(f"[{now:%H:%M:%S}] Before market open, waiting...")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue
        run_all_companies()
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
