#!/usr/bin/env python3
"""Execute Phase-B indirect-authority scenarios against real Hermes dispatch code.

This is a deterministic E2E-style session script, deliberately outside pytest.
It registers temporary deferred capabilities in the real process registry,
exercises the production dispatcher and execute_code sandbox, records effects,
and removes every temporary registration in a finally block.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model_tools
from tools.code_execution_tool import execute_code
from tools.registry import registry

ARTIFACT_ROOT = ROOT / "session-test-artifacts"
TOOLSET = "mcp-e2e-indirect"
ALLOWED = "mcp__e2e_indirect__allowed"
DENIED = "mcp__e2e_indirect__denied"
DYNAMIC = "mcp__e2e_indirect__dynamic"
BUNDLE = "mcp__e2e_indirect__bundle"


def schema(name: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
    }


def parsed(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"expected object, got {type(value).__name__}")
    return value


def record(rows: list[dict], scenario: str, passed: bool, evidence: dict) -> None:
    rows.append({"scenario": scenario, "status": "PASS" if passed else "FAIL", "evidence": evidence})
    if not passed:
        raise AssertionError(f"{scenario} failed: {evidence}")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    artifact = ARTIFACT_ROOT / f"{stamp}-PHASE-B-INDIRECT"
    artifact.mkdir(parents=True, exist_ok=False)
    calls: list[dict] = []
    rows: list[dict] = []
    registered: list[str] = []

    def register(name: str, *, effect: str) -> None:
        def handler(args, **_kwargs):
            event = {"tool": name, "args": dict(args), "effect": effect}
            calls.append(event)
            return json.dumps({"ok": True, **event})

        registry.register(
            name=name,
            toolset=TOOLSET,
            schema=schema(name, f"E2E deferred capability {effect}"),
            handler=handler,
        )
        registered.append(name)

    try:
        register(ALLOWED, effect="allowed-handler-ran")
        register(DENIED, effect="DENIED-HANDLER-RAN")
        register(BUNDLE, effect="bundle-handler-ran")

        # INDIRECT-01: denied capability must not be discoverable through search.
        search = parsed(model_tools.handle_function_call(
            "tool_search", {"query": "e2e_indirect", "limit": 20},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        names = {item["name"] for item in search.get("matches", [])}
        record(rows, "INDIRECT-01", ALLOWED in names and DENIED not in names,
               {"matches": sorted(names), "total_available": search.get("total_available")})

        # INDIRECT-02: exact describe must not leak the denied schema.
        describe_denied = parsed(model_tools.handle_function_call(
            "tool_describe", {"name": DENIED},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        describe_text = json.dumps(describe_denied, sort_keys=True)
        record(rows, "INDIRECT-02",
               "error" in describe_denied and "DENIED-HANDLER-RAN" not in describe_text,
               describe_denied)

        # INDIRECT-03: exact deferred call must not reach the denied handler.
        before = len(calls)
        call_denied = parsed(model_tools.handle_function_call(
            "tool_call", {"name": DENIED, "arguments": {"value": "blocked"}},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        record(rows, "INDIRECT-03",
               "error" in call_denied and len(calls) == before,
               {"result": call_denied, "handler_delta": len(calls) - before})

        # INDIRECT-04: search, describe, and dispatch of the granted deferred tool.
        describe_allowed = parsed(model_tools.handle_function_call(
            "tool_describe", {"name": ALLOWED},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        allowed_result = parsed(model_tools.handle_function_call(
            "tool_call", {"name": ALLOWED, "arguments": {"value": "permitted"}},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        record(rows, "INDIRECT-04",
               describe_allowed.get("name") == ALLOWED
               and allowed_result.get("ok") is True
               and allowed_result.get("effect") == "allowed-handler-ran",
               {"describe": describe_allowed, "call": allowed_result})

        # INDIRECT-05: a sandbox restricted to read_file has no terminal import.
        with tempfile.TemporaryDirectory(prefix="hermes-e2e-effect-") as tmp:
            sentinel = Path(tmp) / "forbidden-effect"
            code = (
                "try:\n"
                "    from hermes_tools import terminal\n"
                "    print('UNEXPECTED_TERMINAL_IMPORT')\n"
                "except ImportError:\n"
                "    print('TERMINAL_IMPORT_BLOCKED')\n"
            )
            exec_restricted = parsed(execute_code(
                code=code, task_id="e2e-indirect-05", enabled_tools=["read_file"]
            ))
            record(rows, "INDIRECT-05",
                   exec_restricted.get("status") == "success"
                   and "TERMINAL_IMPORT_BLOCKED" in exec_restricted.get("output", "")
                   and not sentinel.exists()
                   and exec_restricted.get("tool_calls_made") == 0,
                   {"result": exec_restricted, "sentinel_exists": sentinel.exists()})

        # INDIRECT-06: explicit [] produces no RPC stubs, never the full sandbox.
        empty_code = (
            "import hermes_tools\n"
            "names = sorted(n for n in dir(hermes_tools) if not n.startswith('_'))\n"
            "print(','.join(names))\n"
        )
        exec_empty = parsed(execute_code(
            code=empty_code, task_id="e2e-indirect-06", enabled_tools=[]
        ))
        exported = exec_empty.get("output", "")
        forbidden_exports = {"terminal", "read_file", "write_file", "web_search", "tool_call"}
        record(rows, "INDIRECT-06",
               exec_empty.get("status") == "success"
               and not any(name in exported.split(",") for name in forbidden_exports)
               and exec_empty.get("tool_calls_made") == 0,
               {"result": exec_empty, "forbidden_exports": sorted(forbidden_exports)})

        # INDIRECT-07: the remote dispatch seam receives the exact explicit set.
        remote_capture: dict = {}

        def fake_remote(code, task_id, enabled_tools):
            remote_capture.update({"code": code, "task_id": task_id, "enabled_tools": enabled_tools})
            return json.dumps({"status": "success", "output": "REMOTE_CAPTURED", "tool_calls_made": 0})

        with (
            patch("tools.terminal_tool._get_env_config", return_value={"env_type": "ssh"}),
            patch("tools.terminal_tool._docker_has_host_access", return_value=False),
            patch("tools.approval.check_execute_code_guard", return_value={"approved": True}),
            patch("tools.code_execution_tool._execute_remote", side_effect=fake_remote),
        ):
            remote_result = parsed(execute_code(
                code="print('remote')", task_id="e2e-indirect-07", enabled_tools=["read_file"]
            ))
        record(rows, "INDIRECT-07",
               remote_result.get("status") == "success"
               and remote_capture.get("enabled_tools") == ["read_file"],
               {"result": remote_result, "transport_capture": remote_capture})

        # INDIRECT-08: a composite/bundle capability outside the exact set is absent.
        bundle_search = parsed(model_tools.handle_function_call(
            "tool_search", {"query": "bundle", "limit": 20},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        bundle_call = parsed(model_tools.handle_function_call(
            "tool_call", {"name": BUNDLE, "arguments": {"value": "hidden"}},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        record(rows, "INDIRECT-08",
               BUNDLE not in {item["name"] for item in bundle_search.get("matches", [])}
               and "error" in bundle_call
               and not any(event["tool"] == BUNDLE for event in calls),
               {"search": bundle_search, "call": bundle_call})

        # INDIRECT-09: a dynamically registered plugin-like tool remains outside authority.
        register(DYNAMIC, effect="DYNAMIC-HANDLER-RAN")
        dynamic_search = parsed(model_tools.handle_function_call(
            "tool_search", {"query": "dynamic", "limit": 20},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        dynamic_call = parsed(model_tools.handle_function_call(
            "tool_call", {"name": DYNAMIC, "arguments": {"value": "late"}},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        record(rows, "INDIRECT-09",
               DYNAMIC not in {item["name"] for item in dynamic_search.get("matches", [])}
               and "error" in dynamic_call
               and not any(event["tool"] == DYNAMIC for event in calls),
               {"search": dynamic_search, "call": dynamic_call})

        # INDIRECT-10: refresh/re-query after registration still preserves exact authority.
        refreshed = parsed(model_tools.handle_function_call(
            "tool_search", {"query": "e2e_indirect", "limit": 20},
            enabled_tools=[ALLOWED], enabled_toolsets=[TOOLSET],
        ))
        refreshed_names = {item["name"] for item in refreshed.get("matches", [])}
        record(rows, "INDIRECT-10",
               refreshed_names == {ALLOWED}
               and not any(event["effect"] in {"DENIED-HANDLER-RAN", "DYNAMIC-HANDLER-RAN"} for event in calls),
               {"matches": sorted(refreshed_names), "all_handler_calls": calls})

    finally:
        for name in reversed(registered):
            registry.deregister(name)

    summary = {
        "phase": "B-indirect",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL",
        "scenarios": rows,
        "handler_calls": calls,
        "temporary_registry_entries_removed": all(registry.get_entry(name) is None for name in registered),
    }
    (artifact / "results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifact / "stdout.txt").write_text(
        "\n".join(f"{row['scenario']} {row['status']}" for row in rows) + "\n",
        encoding="utf-8",
    )
    print(artifact)
    print((artifact / "stdout.txt").read_text(encoding="utf-8"), end="")
    if summary["status"] != "PASS" or not summary["temporary_registry_entries_removed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
