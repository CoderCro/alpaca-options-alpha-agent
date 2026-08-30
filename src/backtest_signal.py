"""Signal-only backtest over the trailing month: does rules_engine's 2-of-4
criteria fire, how often, and in which direction, against REAL historical
bars? No options pricing is simulated -- Alpaca's historical options data
needs a paid OPRA/Algo Trader Plus subscription we don't have (confirmed live:
"OPRA agreement is not signed", 403, on both `option bars` and `option
trades`). This validates the shared entry SIGNAL both companies act on, not
P&L -- Company A converts a qualifying signal into a trade mechanically;
Company B reasons over the same signal (among other tool outputs) before
deciding, so this is necessary but not sufficient evidence for Company B's
behavior specifically.

SPX is excluded: Alpaca has no bar data for it at all (`{"bars": null}`, it's
an index, not a bar-generating instrument -- same reason it needs QQQ as a
tradeable proxy elsewhere in this project). Its technical picture tracks SPY
closely enough that a separate computation would be redundant.

Run with: python3 -m src.backtest_signal
"""

from datetime import date, timedelta

import pandas as pd

from src import execution, rules_engine

TICKERS = ["SPY", "QQQ", "BTC/USD", "ETH/USD", "LINK/USD"]
BACKTEST_DAYS = 30

# (Alpaca timeframe string, days of history to fetch) -- generous margins
# over rules_engine's own lookback constants (SR_LOOKBACK_WEEKS=26,
# MA_LOOKBACK_BARS=60, MONTHLY_MA_WINDOW=10) so every backtest day in the
# trailing month still has a full window behind it, not a truncated one.
TIMEFRAMES = {
    "weekly": ("1Week", 400),
    "daily": ("1Day", 250),
    "h4": ("4Hour", 60),
    "m15": ("15Min", 14),
    "monthly": ("1Month", 500),
}


def _fetch_all(ticker: str, end: date) -> dict[str, pd.DataFrame]:
    frames = {}
    for key, (timeframe, lookback_days) in TIMEFRAMES.items():
        start = (end - timedelta(days=lookback_days)).isoformat()
        if "/" in ticker:
            response = execution.get_crypto_bars(ticker, start, end=end.isoformat(), timeframe=timeframe)
            bars = response.get("bars", {}).get(ticker, [])
        else:
            response = execution.get_bars(ticker, start, end=end.isoformat(), timeframe=timeframe, feed="iex")
            bars = response.get("bars", [])
        frames[key] = pd.DataFrame(
            {
                "t": [pd.Timestamp(b["t"]) for b in bars],
                "open": [b["o"] for b in bars],
                "high": [b["h"] for b in bars],
                "low": [b["l"] for b in bars],
                "close": [b["c"] for b in bars],
            }
        )
    return frames


def _as_of(df: pd.DataFrame, as_of: pd.Timestamp, *, fully_elapsed_days: int = 0) -> pd.DataFrame:
    """Bars visible as of a given day. fully_elapsed_days excludes a
    weekly/monthly bar still in progress -- a bar whose period hasn't fully
    closed yet isn't a completed candle, and rules_engine's checks assume one.
    """
    cutoff = as_of - pd.Timedelta(days=fully_elapsed_days)
    return df[df["t"] <= cutoff].reset_index(drop=True)


def run() -> list[dict]:
    end = date.today()
    results = []
    for ticker in TICKERS:
        frames = _fetch_all(ticker, end)
        daily_dates = sorted(t.date() for t in frames["daily"]["t"] if t.date() >= end - timedelta(days=BACKTEST_DAYS))
        for backtest_date in daily_dates:
            as_of_ts = pd.Timestamp(backtest_date, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
            daily_slice = _as_of(frames["daily"], as_of_ts)
            if len(daily_slice) < 3:
                continue
            result = rules_engine.evaluate_criteria(
                weekly_df=_as_of(frames["weekly"], as_of_ts, fully_elapsed_days=7),
                daily_df=daily_slice,
                h4_df=_as_of(frames["h4"], as_of_ts),
                m15_df=_as_of(frames["m15"], as_of_ts),
                monthly_df=_as_of(frames["monthly"], as_of_ts, fully_elapsed_days=31),
            )
            if result.qualifies_for_trading_list:
                results.append(
                    {
                        "ticker": ticker,
                        "date": backtest_date.isoformat(),
                        "direction": result.direction,
                        "criteria_met": [k for k, v in result.met.items() if v],
                        "count_met": result.count_met,
                    }
                )
    return results


def main() -> None:
    results = run()
    print(f"{len(results)} qualifying signal-days over the trailing {BACKTEST_DAYS} days across {TICKERS}\n")
    for r in results:
        print(f"{r['date']}  {r['ticker']:>8}  {r['direction'] or 'none':>7}  ({r['count_met']}/4: {', '.join(r['criteria_met'])})")


if __name__ == "__main__":
    main()
