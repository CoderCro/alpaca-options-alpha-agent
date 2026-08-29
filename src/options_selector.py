"""Deterministic pre-filter over Alpaca's option chain snapshots.

Picking a strike/expiry from a large chain is exactly the kind of numeric-
table reasoning an LLM is least reliable at, so this narrows the chain to a
short, ranked shortlist first -- the agent picks from this shortlist only,
it never gets raw chain data to reason over.

Live-verified against the real Alpaca paper-account feed (not assumed): many
snapshots come back with `greeks.delta == 0` even for contracts that clearly
aren't at-the-money, which reads as "not computed for this snapshot" rather
than a real value. Filtering by delta would therefore silently drop good
candidates, so the hard filters here are DTE (from the OCC symbol) and
strike-to-underlying moneyness -- both always populated. Delta is carried
through on each candidate as extra context for the agent, but only when the
feed gives a usable (nonzero) value.
"""

from dataclasses import dataclass
from datetime import date

DIRECTION_TO_OPTION_TYPE = {"bullish": "C", "bearish": "P"}


@dataclass
class OptionCandidate:
    symbol: str
    expiry: date
    strike: float
    option_type: str  # "C" | "P"
    dte: int
    moneyness: float  # strike / underlying_price; 1.0 == exactly at-the-money
    bid: float
    ask: float
    delta: float | None  # None when the feed doesn't provide a usable value


def parse_occ_symbol(symbol: str) -> tuple[str, date, str, float]:
    """Parses an OCC option symbol into (underlying_root, expiry, option_type, strike).

    Format: <root><YYMMDD><C|P><strike, 8 digits, x1000>. Root length varies,
    so parsing happens from the right -- the last 15 chars are fixed-width.
    """
    root = symbol[:-15]
    date_part = symbol[-15:-9]
    option_type = symbol[-9]
    strike_part = symbol[-8:]
    expiry = date(2000 + int(date_part[0:2]), int(date_part[2:4]), int(date_part[4:6]))
    strike = int(strike_part) / 1000
    return root, expiry, option_type, strike


def select_option_candidates(
    snapshots: dict,
    direction: str,
    underlying_price: float,
    *,
    as_of: date | None = None,
    dte_range: tuple[int, int] = (21, 45),
    moneyness_range: tuple[float, float] = (0.95, 1.05),
    max_candidates: int = 5,
) -> list[OptionCandidate]:
    wanted_type = DIRECTION_TO_OPTION_TYPE.get(direction)
    if wanted_type is None:
        raise ValueError(f"direction must be 'bullish' or 'bearish', got {direction!r}")
    as_of = as_of or date.today()

    candidates = []
    for symbol, snapshot in snapshots.items():
        _root, expiry, option_type, strike = parse_occ_symbol(symbol)
        if option_type != wanted_type:
            continue
        dte = (expiry - as_of).days
        if not (dte_range[0] <= dte <= dte_range[1]):
            continue
        moneyness = strike / underlying_price
        if not (moneyness_range[0] <= moneyness <= moneyness_range[1]):
            continue

        quote = snapshot.get("latestQuote", {})
        delta = snapshot.get("greeks", {}).get("delta")
        candidates.append(
            OptionCandidate(
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                dte=dte,
                moneyness=moneyness,
                bid=quote.get("bp", 0.0),
                ask=quote.get("ap", 0.0),
                delta=delta if delta else None,
            )
        )

    candidates.sort(key=lambda c: abs(c.moneyness - 1.0))
    return candidates[:max_candidates]
