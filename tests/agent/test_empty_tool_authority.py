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
        _raw_authorized_tool_names=set(),
        _exact_tool_allowlist=None,
        _memory_manager=None,
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


def test_invoke_tool_accepts_raw_deferred_authority():
    agent = _agent()
    agent.valid_tool_names = {"tool_search", "tool_describe", "tool_call"}
    agent._raw_authorized_tool_names = {"mcp__late__read"}

    with patch("agent.agent_runtime_helpers._ra") as runtime:
        runtime.return_value.handle_function_call.return_value = '{"ok": true}'
        result = json.loads(
            invoke_tool(
                agent,
                "mcp__late__read",
                {},
                "task",
                skip_tool_request_middleware=True,
                skip_tool_execution_middleware=True,
            )
        )

    assert result == {"ok": True}
    runtime.return_value.handle_function_call.assert_called_once()


def test_invoke_tool_accepts_late_scoped_deferred_authority():
    agent = _agent()
    agent.valid_tool_names = {"tool_search", "tool_describe", "tool_call"}

    tool_def = {
        "type": "function",
        "function": {
            "name": "mcp__late__read",
            "description": "Read from a late MCP server.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    with (
        patch("agent.agent_runtime_helpers._ra") as runtime,
        patch(
            "tools.tool_search.scoped_deferrable_names",
            return_value=frozenset({"mcp__late__read"}),
        ),
    ):
        runtime.return_value.get_tool_definitions.return_value = [tool_def]
        runtime.return_value.handle_function_call.return_value = '{"ok": true}'
        result = json.loads(
            invoke_tool(
                agent,
                "mcp__late__read",
                {},
                "task",
                skip_tool_request_middleware=True,
                skip_tool_execution_middleware=True,
            )
        )

    assert result == {"ok": True}
    runtime.return_value.handle_function_call.assert_called_once()
