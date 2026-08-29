"""Append-only JSONL audit trail.

Extends the codebase's existing "reason string" idiom (CriteriaResult.details,
TradeVerdict.rationale, ExitAction.reason) into a durable record instead of
inventing a new logging pattern. Log the "about to submit" event before
calling execution.submit_order and the result after, so a mid-call crash
still leaves a trail showing an order was attempted. Never log API keys or
other secrets.

The default log_dir is resolved fresh on every call via company_config, not
bound at function-definition time -- see position_store.py's docstring for
why that distinction matters here.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from src import company_config


def log_event(event_type: str, log_dir: Path | None = None, **fields) -> None:
    log_dir = log_dir or company_config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    path = log_dir / f"audit_{today}.jsonl"
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event_type": event_type, **fields}
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")
