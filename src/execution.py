"""Thin subprocess wrapper around Alpaca's official CLI (the `alpaca` binary).

Shells out to the CLI rather than calling Alpaca's REST API directly -- this
is what satisfies the hackathon's requirement that execution go through
Alpaca's own MCP server or CLI tooling. Credentials (ALPACA_API_KEY/
ALPACA_SECRET_KEY) are read by the CLI straight from the environment, so
nothing here touches them.

The CLI always prints JSON to stdout, on success or failure -- the only
signal of failure is the process return code. A non-zero code means the JSON
on stdout is the CLI's error shape ({"error": ..., ...}), not real data, so
that case is raised as AlpacaCliError instead of handed back to the caller.
"""

import json
import subprocess

CLI_BINARY = "alpaca"


class AlpacaCliError(Exception):
    """Raised when the `alpaca` CLI exits non-zero; carries its error message."""


def _run_cli(*args: str) -> dict | list:
    try:
        result = subprocess.run([CLI_BINARY, *args], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise AlpacaCliError(f"alpaca CLI timed out after 30s: {' '.join(args)}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        # stdout empty/non-JSON usually means the CLI died before printing
        # its own JSON error shape (e.g. a transient network failure) --
        # stderr is where that actually shows up; include it or this is
        # undiagnosable from the exception message alone.
        raise AlpacaCliError(
            f"alpaca CLI returned non-JSON output: {result.stdout!r} (exit {result.returncode}, stderr: {result.stderr!r})"
        )
    if result.returncode != 0:
        raise AlpacaCliError(payload.get("error", result.stdout))
    return payload


def get_account() -> dict:
    return _run_cli("account", "get")


def list_positions() -> list[dict]:
    return _run_cli("position", "list")


def _paginated_bars(args: list[str], *, keyed_by_symbol: bool) -> dict:
    """Follows next_page_token until exhausted. Without this, any query whose
    result doesn't fit in one page silently truncates -- confirmed on both
    stock and crypto bars for 4h/15m timeframes over multi-week ranges, which
    would otherwise starve rules_engine's checks of data with no error.
    """
    combined: list | dict = {} if keyed_by_symbol else []
    page_token = None
    while True:
        page_args = args + (["--page-token", page_token] if page_token else [])
        payload = _run_cli(*page_args)
        bars = payload.get("bars") or ({} if keyed_by_symbol else [])
        if keyed_by_symbol:
            for symbol, symbol_bars in bars.items():
                combined.setdefault(symbol, []).extend(symbol_bars)
        else:
            combined.extend(bars)
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return {"bars": combined}


def get_bars(
    symbol: str,
    start: str,
    *,
    end: str | None = None,
    timeframe: str = "1Day",
    limit: int | None = None,
    feed: str | None = None,
) -> dict:
    # feed defaults to the CLI's own default ("sip"), which rejects "recent"
    # dates on accounts without a paid real-time SIP subscription -- pass
    # feed="iex" (the free tier) to avoid that for anything but old history.
    args = ["data", "bars", "--symbol", symbol, "--start", start, "--timeframe", timeframe]
    if end is not None:
        args += ["--end", end]
    if limit is not None:
        args += ["--limit", str(limit)]
    if feed is not None:
        args += ["--feed", feed]
    return _paginated_bars(args, keyed_by_symbol=False)


def get_crypto_bars(
    symbol: str,
    start: str,
    *,
    end: str | None = None,
    timeframe: str = "1Day",
    limit: int | None = None,
) -> dict:
    """symbol is a single pair, e.g. 'BTC/USD' -- wraps the CLI's --symbols
    (plural, comma-separated) since crypto bars is a different subcommand
    from stock bars with a different flag shape."""
    args = ["data", "crypto", "bars", "--symbols", symbol, "--start", start, "--timeframe", timeframe]
    if end is not None:
        args += ["--end", end]
    if limit is not None:
        args += ["--limit", str(limit)]
    return _paginated_bars(args, keyed_by_symbol=True)


def _paginated_option_chain(args: list[str]) -> dict:
    """Follows next_page_token until exhausted -- same silent-truncation risk
    as bars (see _paginated_bars), live-confirmed on SPY: a 21-45 DTE window
    alone returns a next_page_token past the first 100 contracts, and calls
    apparently sort ahead of puts, so an unpaginated fetch can come back with
    zero puts and no error -- exactly the failure Company C hit live, since
    it only ever looks for puts.
    """
    combined: dict = {}
    page_token = None
    while True:
        page_args = args + (["--page-token", page_token] if page_token else [])
        payload = _run_cli(*page_args)
        combined.update(payload.get("snapshots") or {})
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return {"snapshots": combined}


def get_option_chain(
    underlying_symbol: str,
    *,
    option_type: str | None = None,
    expiration_gte: str | None = None,
    expiration_lte: str | None = None,
    strike_gte: float | None = None,
    strike_lte: float | None = None,
) -> dict:
    args = ["data", "option", "chain", "--underlying-symbol", underlying_symbol]
    if option_type is not None:
        args += ["--type", option_type]
    if expiration_gte is not None:
        args += ["--expiration-date-gte", expiration_gte]
    if expiration_lte is not None:
        args += ["--expiration-date-lte", expiration_lte]
    if strike_gte is not None:
        args += ["--strike-price-gte", str(strike_gte)]
    if strike_lte is not None:
        args += ["--strike-price-lte", str(strike_lte)]
    return _paginated_option_chain(args)


def submit_order(
    symbol: str,
    qty: int,
    side: str,
    order_type: str = "limit",
    *,
    limit_price: float | None = None,
    time_in_force: str = "day",
) -> dict:
    args = [
        "order", "submit",
        "--symbol", symbol,
        "--qty", str(qty),
        "--side", side,
        "--type", order_type,
        "--time-in-force", time_in_force,
    ]
    if limit_price is not None:
        args += ["--limit-price", str(limit_price)]
    return _run_cli(*args)


def cancel_order(order_id: str) -> None:
    _run_cli("order", "cancel", "--order-id", order_id)


def get_order(order_id: str) -> dict:
    return _run_cli("order", "get", "--order-id", order_id)


def list_open_orders() -> list[dict]:
    return _run_cli("order", "list", "--status", "open")
