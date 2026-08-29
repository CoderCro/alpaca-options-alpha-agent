"""No real LLM call in this suite -- a fake model is injected exactly like
test_featherless_review.py injects a mock OpenAI client. The one real
Featherless call happens in a separate, manual, non-pytest smoke test.
"""

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from src.trading_agent import MAX_TOOL_TURNS, run_trading_cycle


def _fake_llm(responses):
    bound = MagicMock()
    bound.invoke.side_effect = responses
    llm = MagicMock()
    llm.bind_tools.return_value = bound
    return llm


def test_dispatches_tool_call_and_stops_on_final_answer():
    tool_call_msg = AIMessage(content="", tool_calls=[{"name": "get_account_summary", "args": {}, "id": "call_1"}])
    final_msg = AIMessage(content="No action warranted this cycle.")
    llm = _fake_llm([tool_call_msg, final_msg])

    with patch(
        "src.agent_tools.execution.get_account",
        return_value={"equity": "100000", "buying_power": "400000", "options_trading_level": 3},
    ):
        result = run_trading_cycle(["AAPL"], model=llm)

    assert result["summary"] == "No action warranted this cycle."
    assert result["ran_out_of_turns"] is False
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0].content)["equity"] == 100000.0


def test_unknown_tool_call_does_not_crash():
    tool_call_msg = AIMessage(content="", tool_calls=[{"name": "not_a_real_tool", "args": {}, "id": "call_1"}])
    final_msg = AIMessage(content="done")
    llm = _fake_llm([tool_call_msg, final_msg])

    result = run_trading_cycle(["AAPL"], model=llm)

    assert result["summary"] == "done"


def test_stops_at_iteration_cap_without_crashing():
    always_calls_tool = AIMessage(content="", tool_calls=[{"name": "check_blackout", "args": {}, "id": "call_x"}])
    llm = _fake_llm([always_calls_tool] * (MAX_TOOL_TURNS + 5))

    result = run_trading_cycle(["AAPL"], model=llm)

    assert result["ran_out_of_turns"] is True
    assert "no action" in result["summary"].lower()
    assert llm.bind_tools.return_value.invoke.call_count == MAX_TOOL_TURNS


def test_multiple_tool_calls_in_one_turn_all_dispatched():
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "check_blackout", "args": {}, "id": "call_1"},
            {"name": "get_positions", "args": {}, "id": "call_2"},
        ],
    )
    final_msg = AIMessage(content="done")
    llm = _fake_llm([tool_call_msg, final_msg])

    with patch("src.agent_tools.execution.list_positions", return_value=[]):
        result = run_trading_cycle(["AAPL"], model=llm)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id for m in tool_messages} == {"call_1", "call_2"}
