"""JSON-backed watchlist store: a human-approved trading universe plus a
separate pending-recommendations list.

The agent may recommend additions but can never promote itself onto the
approved list -- recommend_addition only ever writes to `pending`, matching
the README's "agent may recommend additions" to a human-curated list.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "watchlist.json"


@dataclass
class Watchlist:
    approved: set[str] = field(default_factory=set)
    pending: set[str] = field(default_factory=set)


def load(path: Path = DEFAULT_PATH) -> Watchlist:
    if not path.exists():
        return Watchlist()
    data = json.loads(path.read_text())
    return Watchlist(approved=set(data.get("approved", [])), pending=set(data.get("pending", [])))


def save(watchlist: Watchlist, path: Path = DEFAULT_PATH) -> None:
    path.write_text(
        json.dumps({"approved": sorted(watchlist.approved), "pending": sorted(watchlist.pending)}, indent=2)
    )


def recommend_addition(ticker: str, path: Path = DEFAULT_PATH) -> Watchlist:
    """Adds ticker to the pending list only -- never to the approved list."""
    watchlist = load(path)
    if ticker not in watchlist.approved:
        watchlist.pending.add(ticker)
        save(watchlist, path)
    return watchlist
