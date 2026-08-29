"""One-off script: runs a single Company B decision cycle (the fully
autonomous LangChain agent) against its dedicated paper account.

Loads .env first for the shared Featherless credentials, then
.env.company_b with override=True so only the two Alpaca keys change --
see .env.company_b.example.

Run with: python3 -m src.run_company_b
"""

from pathlib import Path

from dotenv import load_dotenv

from src import company_config, trading_agent, watchlist

COMPANY_B_ENV_FILE = Path(__file__).resolve().parent.parent / ".env.company_b"

load_dotenv(".env")
if not load_dotenv(COMPANY_B_ENV_FILE, override=True):
    # Fail loud, not silent -- without this file, Company B would trade on
    # Company A's account using whatever Alpaca keys .env already set.
    raise SystemExit(
        f"{COMPANY_B_ENV_FILE} not found. Copy .env.company_b.example to .env.company_b "
        "and fill in Company B's own Alpaca API key/secret before running this."
    )


def main() -> None:
    company_config.set_company("b")
    tickers = sorted(watchlist.load().approved)
    result = trading_agent.run_trading_cycle(tickers)
    print(result["summary"])


if __name__ == "__main__":
    main()
