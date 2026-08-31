"""One-off script: runs a single Company C decision cycle (the deterministic
delta-neutral vol-edge agent) against its dedicated paper account.

Loads .env first for the shared Featherless credentials, then
.env.company_c with override=True so only the two Alpaca keys change --
see .env.company_c.example.

Run with: python3 -m src.run_company_c
"""

from pathlib import Path

from dotenv import load_dotenv

from src import company_c_agent, company_config, watchlist

COMPANY_C_ENV_FILE = Path(__file__).resolve().parent.parent / ".env.company_c"

load_dotenv(".env")
if not load_dotenv(COMPANY_C_ENV_FILE, override=True):
    # Fail loud, not silent -- without this file, Company C would trade on
    # Company A's account using whatever Alpaca keys .env already set.
    raise SystemExit(
        f"{COMPANY_C_ENV_FILE} not found. Copy .env.company_c.example to .env.company_c "
        "and fill in Company C's own Alpaca API key/secret before running this."
    )


def main() -> None:
    company_config.set_company("c")
    # Same approved watchlist as A/B, not a hardcoded SPY-only list -- the
    # vol-edge signal is a statistical mispricing check, not a "is this a
    # quality name" judgment, so it doesn't need its own narrower universe.
    # Live-verified this doesn't misbehave on the watchlist's non-equity
    # entries: SPX has no bar data (returns cleanly empty, not an error) and
    # crypto tickers have no listed options chain either way, so both fall
    # through to "no signal" safely rather than acting on bad data.
    tickers = sorted(watchlist.load().approved)
    result = company_c_agent.run_trading_cycle(tickers)
    print(result["summary"])


if __name__ == "__main__":
    main()
