"""One-off script: proves Company C's order lifecycle works live, on both
legs (put + stock hedge), without risking a real fill. Places a limit buy on
each deliberately far from the market (won't fill), confirms both show as
open, then cancels both and confirms canceled. Same proof Company A's CLI
wrapper originally got (see SESSION_LOG.md, Aug 29), extended to cover the
equity order path, which nothing has exercised live before now.

Run with: python3 -m src.verify_company_c
"""

from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.company_c", override=True)

from src import execution, options_selector  # noqa: E402  (must follow load_dotenv above)


def main() -> None:
    today = date.today()
    bars = execution.get_bars("SPY", (today - timedelta(days=5)).isoformat(), timeframe="1Day")
    spot = bars["bars"][-1]["c"]

    chain = execution.get_option_chain(
        "SPY",
        expiration_gte=(today + timedelta(days=21)).isoformat(),
        expiration_lte=(today + timedelta(days=45)).isoformat(),
    )
    candidates = options_selector.select_option_candidates(chain.get("snapshots", {}), "bearish", spot, as_of=today)
    put = candidates[0]

    put_limit = round(put.bid * 0.5, 2) or 0.01  # well below bid -- guaranteed not to fill
    stock_limit = round(spot * 0.8, 2)  # well below spot -- guaranteed not to fill

    put_order = execution.submit_order(put.symbol, 1, "buy", limit_price=put_limit)
    stock_order = execution.submit_order("SPY", 1, "buy", limit_price=stock_limit)
    print("PUT order:", put_order["id"], execution.get_order(put_order["id"])["status"])
    print("STOCK order:", stock_order["id"], execution.get_order(stock_order["id"])["status"])

    execution.cancel_order(put_order["id"])
    execution.cancel_order(stock_order["id"])
    print("PUT after cancel:", execution.get_order(put_order["id"])["status"])
    print("STOCK after cancel:", execution.get_order(stock_order["id"])["status"])


if __name__ == "__main__":
    main()
