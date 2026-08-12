import json
from types import SimpleNamespace
from unittest.mock import patch

from agent.agent_runtime_helpers import invoke_tool


def _agent():
    return SimpleNamespace(
        valid_tool_names=set(),
        session_id="test-session",
        _current_turn_id="",
        _current_api_request_id="",
        enabled_toolsets=None,
        disabled_toolsets=None,
    )


def test_invoke_tool_empty_authority_blocks_registry_dispatch():
    agent = _agent()

    with patch("run_agent.handle_function_call") as dispatch:
        result = json.loads(
            invoke_tool(
                agent,
                "terminal",
                {"command": "must not run"},
                "task",
                skip_tool_request_middleware=True,
                skip_tool_execution_middleware=True,
            )
        )

    assert "not authorized" in result["error"]
    dispatch.assert_not_called()


def test_invoke_tool_empty_authority_blocks_inline_agent_tool():
    agent = _agent()
    agent._dispatch_delegate_task = lambda args: "DISPATCHED"

    result = json.loads(
        invoke_tool(
            agent,
            "delegate_task",
            {"goal": "must not run"},
            "task",
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert "not authorized" in result["error"]
