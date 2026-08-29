"""One-off script: runs a single Company A decision cycle (deterministic
rules_engine gate, Featherless veto-only, no LLM execution authority)
against its dedicated paper account.

Run with: python3 -m src.run_company_a
"""

from dotenv import load_dotenv

from src import company_a_agent, company_config, watchlist

load_dotenv()


def main() -> None:
    company_config.set_company("a")
    tickers = sorted(watchlist.load().approved)
    result = company_a_agent.run_trading_cycle(tickers)
    print(result["summary"])
    for action in result["actions"]:
        print(f"  {action}")


if __name__ == "__main__":
    main()
