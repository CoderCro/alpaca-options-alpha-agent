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

Also syncs logs/ to origin/main after every cycle (see sync_audit_logs) so
the deployed Streamlit dashboard's reasoning trail stays current -- it has
no live connection to this machine, it only ever sees whatever was last
pushed. A prior manual-only sync went stale within a day (Company C's
reasoning trail was empty from deployment until someone noticed and asked).

Company C checks more often than A/B (3 min vs. 15): its vol-edge signal is
a live IV-vs-realized-vol comparison on 1-3 DTE contracts that can shift
within a 15-minute gap, unlike A/B's weekly/daily/4h technical reads, which
don't change meaningfully that fast. The main loop ticks at the faster
interval and only runs A/B once enough time has actually elapsed.
"""

import subprocess
import sys
import time
from datetime import datetime
from datetime import time as dtime

from src.calendar_blackout import NY_TZ

AB_CHECK_INTERVAL_SECONDS = 15 * 60
C_CHECK_INTERVAL_SECONDS = 3 * 60
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
COMPANY_AB_SCRIPTS = ["src.run_company_a", "src.run_company_b"]
COMPANY_C_SCRIPT = "src.run_company_c"


def within_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def run_companies(modules: list[str]) -> None:
    for module in modules:
        result = subprocess.run([sys.executable, "-m", module], capture_output=True, text=True)
        status = "ok" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        output = (result.stdout or result.stderr).strip()
        print(f"[{datetime.now(NY_TZ):%H:%M:%S}] {module} [{status}]: {output}")


def sync_audit_logs() -> None:
    """Commits and pushes logs/ so the deployed dashboard's reasoning trail
    stays current. Best-effort, by design: a git/network failure here must
    never crash a trading cycle -- it's a nice-to-have for the demo, not a
    trading safety concern. Skips cleanly (no empty commit) when nothing's
    changed, which is most cycles with no new audit events.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "logs/"], capture_output=True, text=True, check=True
        )
        if not status.stdout.strip():
            return
        subprocess.run(["git", "add", "--", "logs/"], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "Sync audit logs (automated, scheduler)"], check=True, capture_output=True, text=True
        )
        subprocess.run(["git", "push", "origin", "main"], check=True, capture_output=True, text=True)
        print(f"[{datetime.now(NY_TZ):%H:%M:%S}] Audit logs synced to origin/main.")
    except Exception as e:
        print(f"[{datetime.now(NY_TZ):%H:%M:%S}] Audit log sync failed (non-fatal, trading continues): {e}")


def main() -> None:
    print(
        f"Scheduler started {datetime.now(NY_TZ):%Y-%m-%d %H:%M:%S %Z} -- "
        f"Company C every {C_CHECK_INTERVAL_SECONDS // 60} min, A/B every {AB_CHECK_INTERVAL_SECONDS // 60} min, "
        f"exits at market close."
    )
    last_ab_run: float | None = None
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
            time.sleep(C_CHECK_INTERVAL_SECONDS)
            continue

        run_companies([COMPANY_C_SCRIPT])
        if last_ab_run is None or time.time() - last_ab_run >= AB_CHECK_INTERVAL_SECONDS:
            run_companies(COMPANY_AB_SCRIPTS)
            last_ab_run = time.time()
        sync_audit_logs()
        time.sleep(C_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
