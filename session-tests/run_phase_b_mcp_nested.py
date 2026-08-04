#!/usr/bin/env python3
# pyright: reportAttributeAccessIssue=false
"""Execute deterministic Phase-B MCP and nested-authority scenarios.

No model or external MCP server is used. The script exercises Hermes' live
registry, dispatcher, MCP refresh, child-construction and delegation-preflight
boundaries and saves machine-readable evidence outside Git.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model_tools
from tools import async_delegation, mcp_tool
from tools.delegate_tool import _build_child_agent, delegate_task
from tools.registry import registry


def tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"E2E {name}",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }


def register(name: str, toolset: str, calls: list[dict], marker: str) -> None:
    registry.register(
        name=name,
        toolset=toolset,
        schema=tool(name)["function"],
        handler=lambda args, **kw: calls.append({"tool": name, "marker": marker}) or json.dumps(
            {"ok": True, "marker": marker}
        ),
    )


def parent(authority, owners=None, *, depth=0) -> SimpleNamespace:
    return SimpleNamespace(
        base_url="http://127.0.0.1.invalid",
        api_key="[REDACTED]",
        provider="custom",
        api_mode="chat_completions",
        model="cx/gpt-5.6-terra-medium",
        platform="cli",
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection="",
        openrouter_min_coding_score=None,
        request_overrides={},
        reasoning_config=None,
        prefill_messages=None,
        _fallback_chain=None,
        max_tokens=1000,
        enabled_toolsets=None,
        disabled_toolsets=[],
        valid_tool_names=set(authority),
        _exact_tool_allowlist=frozenset(authority),
        _raw_authorized_tool_names=frozenset(authority),
        _exact_mcp_tool_owners=dict(owners or {}),
        _session_db=None,
        _delegate_depth=depth,
        _active_children=[],
        _active_children_lock=threading.Lock(),
        _print_fn=None,
        tool_progress_callback=None,
        thinking_callback=None,
        session_id=f"e2e-parent-{depth}",
        _agent_catalog=None,
        _current_turn_id="e2e-turn",
        _client_kwargs={},
        acp_command=None,
        acp_args=[],
    )


def candidate_child(names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        tools=[tool(name) for name in names],
        valid_tool_names=set(names),
        base_url="http://127.0.0.1.invalid",
        api_key="[REDACTED]",
        provider="custom",
        api_mode="chat_completions",
        model="cx/gpt-5.6-terra-medium",
        platform="subagent",
        providers_allowed=None,
        providers_ignored=None,
        providers_order=None,
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection="",
        openrouter_min_coding_score=None,
        request_overrides={},
        reasoning_config=None,
        prefill_messages=None,
        _fallback_chain=None,
        max_tokens=1000,
        enabled_toolsets=None,
        disabled_toolsets=[],
        _session_db=None,
        session_id="e2e-child",
        _session_init_model_config={},
        _active_children=[],
        _active_children_lock=threading.Lock(),
        _tool_search_scope_cache=None,
        _print_fn=None,
        tool_progress_callback=None,
        thinking_callback=None,
        _current_turn_id="e2e-child-turn",
        _client_kwargs={},
        acp_command=None,
        acp_args=[],
    )


def build(parent_agent, names, authority=None, owners=None, role="orchestrator"):
    child = candidate_child(names)
    if owners is not None:
        parent_agent._exact_mcp_tool_owners = dict(owners)
    with (
        patch("run_agent.AIAgent", return_value=child),
        patch("tools.delegate_tool._get_orchestrator_enabled", return_value=True),
        patch("tools.delegate_tool._get_max_spawn_depth", return_value=4),
        patch("tools.delegate_tool._load_config", return_value={}),
    ):
        built = _build_child_agent(
            task_index=0,
            goal="E2E nested authority",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=2,
            parent_agent=parent_agent,
            task_count=1,
            role=role,
            exact_tool_allowlist=set(authority) if authority is not None else None,
        )
    return built


def row(rows, sid, passed, evidence):
    rows.append({"scenario": sid, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def main() -> int:
    artifact = ROOT / "session-test-artifacts" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-PHASE-B-MCP-NESTED"
    )
    artifact.mkdir(parents=True, mode=0o700)
    rows: list[dict] = []
    calls: list[dict] = []
    registered: list[str] = []
    history_before = dict(mcp_tool._mcp_tool_server_history)
    current_before = dict(mcp_tool._mcp_tool_server_names)

    context_name = "mcp__e2e_context7__query"
    other_name = "mcp__e2e_other__query"
    collision_name = "mcp__e2e_team_a__status"
    plugin_name = "mcp__e2e_plugin_substitution__status"
    concurrent_name = "mcp__e2e_concurrent__status"
    try:
        for name, toolset, marker in (
            (context_name, "mcp-context7", "context7"),
            (other_name, "mcp-other", "other"),
            (collision_name, "mcp-team-a", "team-a"),
            (plugin_name, "mcp-original", "original"),
            (concurrent_name, "mcp-concurrent", "original"),
        ):
            register(name, toolset, calls, marker)
            registered.append(name)
        mcp_tool._track_mcp_tool_server(context_name, "context7")
        mcp_tool._track_mcp_tool_server(other_name, "other")
        mcp_tool._track_mcp_tool_server(collision_name, "team-a")
        mcp_tool._track_mcp_tool_server(plugin_name, "original")
        mcp_tool._track_mcp_tool_server(concurrent_name, "concurrent")

        parsed = json.loads(model_tools.handle_function_call(
            context_name, {}, enabled_tools=[context_name],
            authorized_mcp_owners={context_name: "context7"},
        ))
        row(rows, "MCP-01", parsed.get("marker") == "context7", parsed)

        before = len(calls)
        denied = json.loads(model_tools.handle_function_call(
            other_name, {}, enabled_tools=[context_name],
            authorized_mcp_owners={context_name: "context7"},
        ))
        row(rows, "MCP-02", "not authorized" in denied.get("error", "") and len(calls) == before,
            {"result": denied, "handler_delta": len(calls) - before})

        conflict = mcp_tool._mcp_tool_owner_conflicts(collision_name, "team_a")
        same = mcp_tool._mcp_tool_owner_conflicts(collision_name, "team-a")
        row(rows, "MCP-03", conflict and not same,
            {"different_raw_owner_conflicts": conflict, "same_raw_owner_conflicts": same})

        refresh_agent = SimpleNamespace(
            tools=[tool(context_name)], valid_tool_names={context_name},
            enabled_toolsets=None, disabled_toolsets=[],
            _exact_tool_allowlist=frozenset({context_name}),
            _exact_mcp_tool_owners={context_name: "context7"},
            _tool_search_scope_cache=None,
        )
        with patch("model_tools.get_tool_definitions", return_value=[tool(context_name)]):
            added = mcp_tool.refresh_agent_mcp_tools(refresh_agent)
        refresh_authority = set(refresh_agent.valid_tool_names) - {
            "tool_search", "tool_describe", "tool_call"
        }
        row(rows, "MCP-04", refresh_authority == {context_name},
            {
                "execution_authority": sorted(refresh_authority),
                "visible_bridges_added": sorted(added),
                "valid": sorted(refresh_agent.valid_tool_names),
            })

        registry.deregister(plugin_name)
        registered.remove(plugin_name)
        mcp_tool._forget_mcp_tool_server(plugin_name)
        register(plugin_name, "e2e-plugin", calls, "plugin-replacement")
        registered.append(plugin_name)
        before = len(calls)
        substitution = json.loads(model_tools.handle_function_call(
            plugin_name, {}, enabled_tools=[plugin_name],
            authorized_mcp_owners={plugin_name: "original"},
        ))
        row(rows, "MCP-05", "provenance changed" in substitution.get("error", "") and len(calls) == before,
            {"result": substitution, "handler_delta": len(calls) - before})

        mcp_tool._mcp_tool_server_names[collision_name] = "team_a"
        before = len(calls)
        wrong_server = json.loads(model_tools.handle_function_call(
            collision_name, {}, enabled_tools=[collision_name],
            authorized_mcp_owners={collision_name: "team-a"},
        ))
        row(rows, "MCP-06", "provenance changed" in wrong_server.get("error", "") and len(calls) == before,
            {"result": wrong_server, "handler_delta": len(calls) - before})

        race_agent = SimpleNamespace(
            tools=[tool(concurrent_name)], valid_tool_names={concurrent_name},
            enabled_toolsets=None, disabled_toolsets=[],
            _exact_tool_allowlist=frozenset({concurrent_name}),
            _exact_mcp_tool_owners={concurrent_name: "concurrent"},
            _tool_search_scope_cache=None,
        )
        race_errors: list[str] = []
        race_markers: list[str] = []
        barrier = threading.Barrier(2)
        def refresher():
            barrier.wait()
            for _ in range(20):
                with patch("model_tools.get_tool_definitions", return_value=[tool(concurrent_name)]):
                    mcp_tool.refresh_agent_mcp_tools(race_agent)
        def caller():
            barrier.wait()
            for _ in range(20):
                result = json.loads(model_tools.handle_function_call(
                    concurrent_name, {}, enabled_tools=list(race_agent._exact_tool_allowlist),
                    authorized_mcp_owners=dict(race_agent._exact_mcp_tool_owners),
                ))
                if result.get("marker"):
                    race_markers.append(result["marker"])
                elif result.get("error"):
                    race_errors.append(result["error"])
        threads = [threading.Thread(target=refresher), threading.Thread(target=caller)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        row(rows, "MCP-07", set(race_markers) <= {"original"} and len(race_markers) + len(race_errors) == 20,
            {"markers": race_markers, "errors": race_errors})

        before = len(calls)
        restored = json.loads(model_tools.handle_function_call(
            plugin_name, {}, enabled_tools=[plugin_name],
            authorized_mcp_owners={plugin_name: "original"},
        ))
        row(rows, "MCP-08", "provenance changed" in restored.get("error", "") and len(calls) == before,
            {"result": restored, "handler_delta": len(calls) - before})

        # Nested authority: exercise actual child-construction boundary.
        names = ["delegate_task", "read_file", "terminal", context_name]
        p1 = parent({"delegate_task", "read_file"})
        c1 = build(p1, names)
        row(rows, "NEST-01", c1._exact_tool_allowlist == frozenset({"delegate_task", "read_file"}),
            {"authority": sorted(c1._exact_tool_allowlist)})

        p2 = parent({"delegate_task", "read_file"})
        c2 = build(p2, names, authority=None)
        row(rows, "NEST-02", "terminal" not in c2.valid_tool_names and c2._exact_tool_allowlist == frozenset({"delegate_task", "read_file"}),
            {"authority": sorted(c2._exact_tool_allowlist)})

        p3 = parent({"delegate_task", "read_file", "terminal"})
        c3 = build(p3, names, authority={"read_file"})
        row(rows, "NEST-03", c3._exact_tool_allowlist == frozenset({"read_file"}),
            {"authority": sorted(c3._exact_tool_allowlist)})

        p4 = parent(set())
        c4 = build(p4, names, authority=set())
        row(rows, "NEST-04", c4._exact_tool_allowlist == frozenset() and c4.valid_tool_names == set(),
            {"authority": sorted(c4._exact_tool_allowlist), "valid": sorted(c4.valid_tool_names)})

        p5 = parent({"delegate_task", "read_file"})
        child5 = build(p5, names)
        grandchild5 = build(child5, names)
        direct = json.loads(model_tools.handle_function_call("terminal", {"command": "forbidden"}, enabled_tools=list(grandchild5._exact_tool_allowlist)))
        row(rows, "NEST-05", "not authorized" in direct.get("error", "") and "terminal" not in grandchild5.valid_tool_names,
            {"authority": sorted(grandchild5._exact_tool_allowlist), "result": direct})

        indirect = json.loads(model_tools.handle_function_call(
            "tool_call", {"name": other_name, "arguments": {}},
            enabled_tools=list(grandchild5._exact_tool_allowlist),
        ))
        row(rows, "NEST-06", "not available in this session" in indirect.get("error", ""), indirect)

        p7 = parent({"delegate_task", context_name}, {context_name: "context7"})
        child7 = build(p7, names, owners={context_name: "context7"})
        grandchild7 = build(child7, names)
        row(rows, "NEST-07", grandchild7._exact_mcp_tool_owners == {context_name: "context7"},
            {"owners": grandchild7._exact_mcp_tool_owners})

        first = SimpleNamespace(name="first", provider="provider-a", model="model-a", tools_allow=None, mcp_allow=None)
        second = SimpleNamespace(name="second", provider="provider-b", model="model-b", tools_allow=None, mcp_allow=None)
        p8 = parent({"delegate_task", "read_file"})
        with (
            patch("tools.delegate_tool._resolve_selected_agent", side_effect=[first, second]),
            patch("tools.delegate_tool._resolve_delegation_credentials", side_effect=[
                {"provider": None, "model": None, "base_url": None, "api_key": None, "api_mode": None},
                {"provider": "provider-a", "model": "model-a", "base_url": "a", "api_key": "[REDACTED]", "api_mode": None},
                ValueError("missing credential"),
            ]),
            patch("tools.delegate_tool._build_child_preserving_parent_tools") as build_mock,
        ):
            batch_result = json.loads(delegate_task(
                tasks=[{"goal": "First", "agent_name": "first"}, {"goal": "Second", "agent_name": "second"}],
                parent_agent=p8,
            ))
        row(rows, "NEST-08", "AGENT_ROUTE_UNAVAILABLE" in batch_result.get("error", "") and build_mock.call_count == 0,
            {"result": batch_result, "children_built": build_mock.call_count})

        interrupt_count = [0]
        async_delegation._reset_for_tests()
        with async_delegation._records_lock:
            async_delegation._records["e2e-nested-cancel"] = {
                "status": "running", "interrupt_fn": lambda: interrupt_count.__setitem__(0, interrupt_count[0] + 1),
                "parent_session_id": "e2e-cancel-parent",
            }
        interrupted = async_delegation.interrupt_for_session(parent_session_id="e2e-cancel-parent", reason="e2e")
        row(rows, "NEST-09", interrupted == 1 and interrupt_count[0] == 1,
            {"interrupted": interrupted, "interrupt_calls": interrupt_count[0]})
        async_delegation._reset_for_tests()

        # Accounting oracle through the real delegate_task finalization path.
        accounting_parent = parent({"delegate_task"})
        accounting_parent.session_estimated_cost_usd = 1.0
        accounting_parent.session_cost_status = "unknown"
        accounting_parent.session_cost_source = "none"
        with patch("tools.delegate_tool._run_single_child", return_value={
            "task_index": 0, "status": "completed", "summary": "done",
            "api_calls": 1, "duration_seconds": 0.1, "_child_role": "leaf",
            "_child_cost_usd": 0.25,
        }):
            accounting_result = json.loads(delegate_task(goal="Accounting", parent_agent=accounting_parent))
        row(rows, "NEST-10", (
            accounting_parent.session_estimated_cost_usd == 1.25
            and accounting_parent.session_cost_source == "subagent"
            and accounting_parent.session_cost_status == "estimated"
            and "_child_cost_usd" not in accounting_result["results"][0]
        ), {
            "cost": accounting_parent.session_estimated_cost_usd,
            "cost_source": accounting_parent.session_cost_source,
            "cost_status": accounting_parent.session_cost_status,
            "serialized_result": accounting_result,
        })

    finally:
        for name in list(registered):
            registry.deregister(name)
        mcp_tool._mcp_tool_server_history.clear()
        mcp_tool._mcp_tool_server_history.update(history_before)
        mcp_tool._mcp_tool_server_names.clear()
        mcp_tool._mcp_tool_server_names.update(current_before)

    summary = {
        "phase": "B-mcp-nested",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(item["status"] == "PASS" for item in rows) else "FAIL",
        "scenarios": rows,
        "handler_calls": calls,
        "temporary_registry_entries_removed": all(registry.get_entry(name) is None for name in registered),
    }
    (artifact / "results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifact / "stdout.txt").write_text("\n".join(f"{r['scenario']} {r['status']}" for r in rows) + "\n", encoding="utf-8")
    print(artifact)
    for item in rows:
        print(item["scenario"], item["status"])
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
