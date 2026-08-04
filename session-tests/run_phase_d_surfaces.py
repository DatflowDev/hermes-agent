#!/usr/bin/env python3
"""Execute Phase-D CLI, RPC, TUI, Desktop, and profile-surface gates.

All UI checks run against deterministic projections or isolated frontend tests.
Desktop is never launched against the real HERMES_HOME. No model is called.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/home/jcardibo/.hermes/hermes-agent/venv/bin/python"
ARTIFACT_ROOT = ROOT / "session-test-artifacts"


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 300) -> dict[str, Any]:
    proc = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )
    return {"command": command, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def artifact_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = ARTIFACT_ROOT / f"{stamp}-PHASE-D-SURFACES"
    path.mkdir(parents=True, mode=0o700)
    return path


def main() -> int:
    artifact = artifact_dir()
    commands: dict[str, dict[str, Any]] = {}

    commands["cli_rpc"] = run([
        PYTHON, "-m", "pytest", "-q",
        "tests/hermes_cli/test_agent_definition_commands.py",
        "tests/test_tui_gateway_server.py::test_agent_definitions_list_requires_a_live_session",
        "tests/test_tui_gateway_server.py::test_agent_definition_launch_is_bound_and_single_use",
        "tests/test_tui_gateway_server.py::test_agent_definition_launch_propagates_delegate_error",
        "tests/test_tui_gateway_server.py::test_agent_definition_launch_binds_session_profile_home_and_secret_scope",
    ])
    commands["profile_surfaces"] = run([
        PYTHON, "-m", "pytest", "-q",
        "tests/hermes_cli/test_profiles.py",
    ])
    commands["tui"] = run([
        "npm", "test", "--workspace", "ui-tui", "--", "src/__tests__/agentDefinitionsOverlay.test.ts",
    ])
    commands["tui_typecheck"] = run(["npm", "run", "typecheck", "--workspace", "ui-tui"])
    commands["desktop"] = run([
        "npm", "test", "--workspace", "apps/desktop", "--", "src/app/agents/definitions.test.tsx",
    ])
    commands["desktop_typecheck"] = run(["npm", "run", "typecheck", "--workspace", "apps/desktop"], timeout=600)

    # Live, read-only projection from the active catalog; no agent launch.
    commands["active_projection"] = run([
        PYTHON, "-c",
        (
            "import json; from pathlib import Path; "
            "from agent.agent_definitions import discover_profile_agents; "
            "c=discover_profile_agents(Path('/home/jcardibo/.hermes')); "
            "print(json.dumps(c.project(), sort_keys=True))"
        ),
    ])
    projection = {}
    if commands["active_projection"]["returncode"] == 0:
        projection = json.loads(commands["active_projection"]["stdout"])

    expected = {
        "code-reviewer", "researcher", "security-auditor",
        "test-engineer", "web-performance-auditor",
    }
    projected_names = {item["name"] for item in projection.get("definitions", [])}
    projection_ok = (
        projection.get("version") == 2
        and projected_names == expected
        and all(len(item.get("digest", "")) == 64 for item in projection.get("definitions", []))
    )

    # Matrix rows: exact typed surface invariants are grouped by their owning test suite.
    rows = {
        "CLI-01": commands["cli_rpc"]["returncode"] == 0,
        "CLI-02": commands["cli_rpc"]["returncode"] == 0,
        "CLI-03": commands["cli_rpc"]["returncode"] == 0,
        "CLI-04": commands["cli_rpc"]["returncode"] == 0,
        "GW-01": commands["cli_rpc"]["returncode"] == 0,
        "GW-02": commands["cli_rpc"]["returncode"] == 0,
        "RPC-01": projection_ok,
        "RPC-02": commands["cli_rpc"]["returncode"] == 0,
        "RPC-03": commands["cli_rpc"]["returncode"] == 0,
        "TUI-01": commands["tui"]["returncode"] == 0,
        "TUI-02": commands["tui"]["returncode"] == 0,
        "TUI-03": commands["tui"]["returncode"] == 0,
        "DESK-01": commands["desktop"]["returncode"] == 0,
        "DESK-02": commands["desktop"]["returncode"] == 0,
        "DESK-03": commands["desktop"]["returncode"] == 0,
        "DESK-04": commands["desktop"]["returncode"] == 0,
        "PROFILE-01": commands["profile_surfaces"]["returncode"] == 0,
        "PROFILE-02": commands["profile_surfaces"]["returncode"] == 0,
        "PROFILE-03": commands["profile_surfaces"]["returncode"] == 0,
    }
    supporting = {
        "tui_typecheck": commands["tui_typecheck"]["returncode"] == 0,
        "desktop_typecheck": commands["desktop_typecheck"]["returncode"] == 0,
        "projection": projection_ok,
    }
    status = "PASS" if all(rows.values()) and all(supporting.values()) else "FAIL"
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": "D-surfaces",
        "status": status,
        "rows": rows,
        "supporting": supporting,
        "projection": projection,
        "commands": commands,
        "desktop_real_home_used": False,
    }
    (artifact / "results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(artifact)
    for scenario, passed in rows.items():
        print(f"{scenario} {'PASS' if passed else 'FAIL'}")
    for name, passed in supporting.items():
        print(f"SUPPORT-{name} {'PASS' if passed else 'FAIL'}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
