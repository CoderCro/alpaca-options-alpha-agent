"""JSON-backed store linking a Company C put position to its delta-hedge
stock position -- Alpaca's own position list has no notion that these two
separate positions (a put, a block of shares) are one economic trade, same
reason position_store.py exists for the exit ladder.

Company C doesn't use position_manager.py's scaled exit ladder (Stage/
Position) -- it's a single entry and a single thesis-driven exit, not a
tranche-by-tranche scale-out -- so this is a smaller, separate shape rather
than force-fitting the ladder's state machine onto a strategy without tranches.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src import company_config

FILENAME = "hedge_state.json"


@dataclass
class HedgePosition:
    put_symbol: str
    underlying_symbol: str
    put_qty: int
    hedge_shares: int
    entry_realized_vol: float
    entry_implied_vol: float


def load_all(path: Path | None = None) -> dict[str, HedgePosition]:
    path = path or company_config.state_path(FILENAME)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {symbol: HedgePosition(**p) for symbol, p in data.items()}


def save_all(positions: dict[str, HedgePosition], path: Path | None = None) -> None:
    path = path or company_config.state_path(FILENAME)
    data = {symbol: asdict(p) for symbol, p in positions.items()}
    path.write_text(json.dumps(data, indent=2))


def record(position: HedgePosition, path: Path | None = None) -> None:
    positions = load_all(path)
    positions[position.put_symbol] = position
    save_all(positions, path)


def remove(put_symbol: str, path: Path | None = None) -> None:
    positions = load_all(path)
    positions.pop(put_symbol, None)
    save_all(positions, path)
