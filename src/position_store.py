"""JSON-backed store for exit-ladder state (position_manager.Position).

Alpaca's own position list has no notion of our custom exit-ladder stage
(TRANCHE_1_DONE, TAIL_VIA_STOP, ...) -- that state has to be tracked
separately, keyed by option symbol, alongside Alpaca's own record of what's
actually held.

The default path is resolved fresh on every call via company_config, not
bound at function-definition time -- company_config.set_company() is called
by each company's entry-point script after this module is already imported,
so a hardcoded default parameter would never see the update.
"""

import json
from pathlib import Path

from src import company_config
from src.position_manager import Position, Stage

FILENAME = "position_state.json"


def load_all(path: Path | None = None) -> dict[str, Position]:
    path = path or company_config.state_path(FILENAME)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        symbol: Position(
            entry_price=p["entry_price"],
            original_qty=p["original_qty"],
            remaining_qty=p["remaining_qty"],
            stage=Stage[p["stage"]],
        )
        for symbol, p in data.items()
    }


def save_all(positions: dict[str, Position], path: Path | None = None) -> None:
    path = path or company_config.state_path(FILENAME)
    data = {
        symbol: {
            "entry_price": p.entry_price,
            "original_qty": p.original_qty,
            "remaining_qty": p.remaining_qty,
            "stage": p.stage.name,
        }
        for symbol, p in positions.items()
    }
    path.write_text(json.dumps(data, indent=2))


def record_new_position(symbol: str, entry_price: float, qty: int, path: Path | None = None) -> None:
    path = path or company_config.state_path(FILENAME)
    positions = load_all(path)
    positions[symbol] = Position(entry_price=entry_price, original_qty=qty, remaining_qty=qty, stage=Stage.OPEN)
    save_all(positions, path)


def update_after_exit(symbol: str, remaining_qty: int, stage: Stage, path: Path | None = None) -> None:
    path = path or company_config.state_path(FILENAME)
    positions = load_all(path)
    if symbol in positions:
        positions[symbol].remaining_qty = remaining_qty
        positions[symbol].stage = stage
        save_all(positions, path)
