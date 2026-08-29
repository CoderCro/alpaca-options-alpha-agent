"""One-off script: confirms ALPACA_API_KEY/ALPACA_SECRET_KEY work and prints
the paper account's ID and balance -- the account ID is required later for
the hackathon submission.

Run with: python3 -m src.check_alpaca_connection
"""

import os

from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    api_key = os.environ["ALPACA_API_KEY"]
    secret_key = os.environ["ALPACA_SECRET_KEY"]

    client = TradingClient(api_key, secret_key, paper=True)
    account = client.get_account()

    print(f"Connected OK")
    print(f"Account ID:      {account.id}")
    print(f"Status:          {account.status}")
    print(f"Equity:          ${float(account.equity):,.2f}")
    print(f"Buying power:    ${float(account.buying_power):,.2f}")
    print(f"Pattern day trader: {account.pattern_day_trader}")


if __name__ == "__main__":
    main()
