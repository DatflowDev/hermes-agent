#!/usr/bin/env python3
"""Capture Phase-E Voice evidence and validate live agent delegation logs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = ROOT / "session-test-artifacts"
LOG_TOOL = re.compile(r"^\d\d:\d\d:\d\d tool\s+\| -> ([A-Za-z0-9_.-]+)", re.MULTILINE)
EXPECTED_HERMES_HOME = Path.home() / ".hermes" / "profiles" / "hermes-agents-skills-test"


def artifact_dir(root: Path, scenario: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = root / f"{stamp}-{scenario}"
    path.mkdir(parents=True, mode=0o700)
    return path


def run_voice(artifact: Path) -> dict:
    tests = [
        "tests/cli/test_cli_interrupt_ack_race.py::test_voice_chat_persists_clean_input_when_concise_guidance_is_enabled",
        "tests/cli/test_cli_interrupt_ack_race.py::test_voice_chat_matches_typed_input_when_concise_guidance_is_disabled",
        "tests/cli/test_cli_interrupt_ack_race.py::test_typed_chat_never_gets_voice_guidance_when_setting_is_enabled",
        "tests/tools/test_voice_cli_integration.py::TestVoiceFullDuplexListener::test_generation_trip_interrupts_agent_and_submits",
        "tests/tools/test_voice_cli_integration.py::TestVoiceFullDuplexListener::test_stop_phrase_mid_generation_interrupts_and_ends_chat",
        "tests/tools/test_voice_cli_integration.py::TestTypedVoiceStop::test_longer_typed_message_passes_through_in_voice_mode",
    ]
    runs = []
    for test in tests:
        command = [sys.executable, "-m", "pytest", "-q", test]
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        runs.append({"command": command, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
    (artifact / "voice.stdout.txt").write_text(
        "\n".join(run["stdout"] for run in runs), encoding="utf-8"
    )
    (artifact / "voice.stderr.txt").write_text(
        "\n".join(run["stderr"] for run in runs), encoding="utf-8"
    )
    passed = all(run["returncode"] == 0 and "1 passed" in run["stdout"] for run in runs)
    return {
        "VOICE-01": passed,
        "VOICE-02": passed,
        "VOICE-03": passed,
        "VOICE-04": passed,
        "VOICE-05": passed,
        "VOICE-06": passed,
        "VOICE-07": passed,
        "runs": runs,
        "passed": passed,
    }


def child_models(state_db: Path, parent_session_ids: list[str]) -> dict[str, str]:
    if not parent_session_ids:
        return {}
    placeholders = ",".join("?" for _ in parent_session_ids)
    with sqlite3.connect(state_db) as conn:
        rows = conn.execute(
            f"SELECT id, model FROM sessions WHERE parent_session_id IN ({placeholders})",
            parent_session_ids,
        ).fetchall()
    return {session_id: model for session_id, model in rows}


def validate_logs(
    hermes_home: Path,
    delegation_ids: list[str],
    parent_session_ids: list[str],
    artifact: Path,
) -> dict:
    hermes_home = hermes_home.expanduser().resolve(strict=True)
    if hermes_home != EXPECTED_HERMES_HOME.resolve():
        raise ValueError(f"Phase E requires the dedicated profile: {EXPECTED_HERMES_HOME}")
    expected = {
        "researcher": {"allowed": {"read_file", "web_search", "web_extract", "search_files"}, "forbidden": {"terminal", "write_file", "patch"}},
        "code-reviewer": {"allowed": {"read_file", "search_files", "terminal", "web_search", "web_extract"}, "forbidden": {"write_file", "patch"}},
        "security-auditor": {"allowed": {"read_file", "search_files", "terminal", "web_search", "web_extract"}, "forbidden": {"write_file", "patch"}},
        "test-engineer": {"allowed": {"read_file", "search_files", "terminal", "write_file", "patch"}, "forbidden": {"browser_navigate", "web_search"}},
        "web-performance-auditor": {"allowed": {"web_search", "web_extract", "browser_navigate", "browser_snapshot", "browser_console", "browser_get_images", "browser_vision"}, "forbidden": {"terminal", "write_file", "patch", "read_file"}},
    }
    identity_markers = {
        "researcher": "rigorous technical research agent",
        "code-reviewer": "Senior Code Reviewer",
        "security-auditor": "Security Auditor",
        "test-engineer": "Test Engineer",
        "web-performance-auditor": "Web Performance Auditor",
    }
    models = child_models(hermes_home / "state.db", parent_session_ids)
    results: dict[str, dict] = {}
    for delegation_id in delegation_ids:
        live = hermes_home / "cache" / "delegation" / "live" / delegation_id
        manifest = json.loads((live / "manifest.json").read_text(encoding="utf-8"))
        for task in manifest["tasks"]:
            log = Path(task["log"]).read_text(encoding="utf-8")
            name_match = re.search(r"E2E_AGENT=([a-z-]+)", log)
            if not name_match:
                raise RuntimeError(f"{delegation_id} task {task['index']}: missing E2E_AGENT marker")
            name = name_match.group(1)
            tools = set(LOG_TOOL.findall(log))
            policy = expected[name]
            forbidden_seen = sorted(tools & policy["forbidden"])
            allowed_seen = sorted(tools & policy["allowed"])
            model_matches = [model for session_id, model in models.items() if session_id in log]
            if len(model_matches) > 1:
                raise RuntimeError(f"{delegation_id} task {task['index']}: ambiguous child session")
            if not model_matches:
                # Live transcripts do not print the child session id. Identity order is
                # unambiguous in state.db, so bind by the selected identity prompt.
                with sqlite3.connect(hermes_home / "state.db") as conn:
                    rows = conn.execute(
                        "SELECT model FROM sessions WHERE parent_session_id IN (%s) "
                        "AND system_prompt LIKE ? ORDER BY started_at"
                        % ",".join("?" for _ in parent_session_ids),
                        [*parent_session_ids, f"%{identity_markers[name]}%"],
                    ).fetchall()
                if len(rows) != 1:
                    raise RuntimeError(
                        f"{delegation_id} task {task['index']}: expected exactly one identity-bound child session"
                    )
                model = rows[0][0]
            else:
                model = model_matches[0]
            model_ok = bool(model) and model.startswith(("cx/gpt-5.6-terra-", "cx/gpt-5.6-luna-"))
            passed = task["status"] == "completed" and bool(allowed_seen) and not forbidden_seen and model_ok
            results[name] = {
                "delegation_id": delegation_id,
                "task_index": task["index"],
                "status": "PASS" if passed else "FAIL",
                "manifest_status": task["status"],
                "tools": sorted(tools),
                "allowed_seen": allowed_seen,
                "forbidden_seen": forbidden_seen,
                "model": model,
                "model_ok": model_ok,
            }
            (artifact / f"{name}.log").write_text(log, encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--delegation-id", action="append", default=[])
    parser.add_argument("--parent-session-id", action="append", default=[])
    parser.add_argument("--voice-only", action="store_true")
    args = parser.parse_args()

    if not args.voice_only:
        if not args.delegation_id or not args.parent_session_id:
            parser.error("agent validation requires delegation and parent session IDs")
        args.hermes_home = args.hermes_home.expanduser().resolve(strict=True)
        if args.hermes_home != EXPECTED_HERMES_HOME.resolve():
            parser.error(f"--hermes-home must be the dedicated profile: {EXPECTED_HERMES_HOME}")

    artifact = artifact_dir(args.artifact_root.resolve(), "PHASE-E-VOICE-AGENTS")
    voice = run_voice(artifact)
    agents = {} if args.voice_only else validate_logs(
        args.hermes_home.resolve(), args.delegation_id, args.parent_session_id, artifact
    )
    summary = {
        "phase": "E-voice-agents",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "voice": voice,
        "agents": agents,
        "status": "PASS" if voice["passed"] and all(row["status"] == "PASS" for row in agents.values()) else "FAIL",
    }
    (artifact / "results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(artifact)
    for key in sorted(key for key in voice if key.startswith("VOICE-")):
        print(f"{key} {'PASS' if voice[key] else 'FAIL'}")
    for name, row in sorted(agents.items()):
        print(f"AGENT-{name} {row['status']}")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
