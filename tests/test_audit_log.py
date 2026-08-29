import json

from src.audit_log import log_event


def test_log_event_appends_jsonl_line(tmp_path):
    log_event("tool_call", log_dir=tmp_path, tool="get_account", args={})
    files = list(tmp_path.glob("audit_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_type"] == "tool_call"
    assert record["tool"] == "get_account"
    assert "timestamp" in record


def test_log_event_appends_multiple_events_same_day(tmp_path):
    log_event("tool_call", log_dir=tmp_path, tool="a")
    log_event("gate_result", log_dir=tmp_path, gate="blackout", allowed=True)
    files = list(tmp_path.glob("audit_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 2
