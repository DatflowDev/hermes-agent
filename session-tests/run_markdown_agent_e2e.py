#!/usr/bin/env python3
"""Isolated evidence runner for Markdown-agent end-to-end session scenarios.

This is intentionally outside pytest discovery. It captures and validates real
Hermes delegation traces without treating model prose as the oracle.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROVIDER = "omniroute-gpt"
DEFAULT_MODEL = "cx/gpt-5.6-terra-medium"
DEFAULT_REASONING = "low"
DEFAULT_MAX_CALLS = 4
DEFAULT_TIMEOUT_SECONDS = 90

_SECRET_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"authorization\s*:",
        r"\b(?:api[_-]?key|password|token|secret)\s*[:=]",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
    )
)


def reject_secrets(text: str) -> None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise ValueError("secret-like input is forbidden in E2E artifacts")


def parse_csv_names(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def create_artifact_bundle(root: Path, *, scenario_id: str, metadata: dict[str, Any]) -> Path:
    reject_secrets(json.dumps(metadata, sort_keys=True))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", scenario_id)
    artifact = root / f"{stamp}-{safe_id}"
    artifact.mkdir(parents=True, exist_ok=False)
    payload = {
        "scenario_id": scenario_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    _write_json(artifact / "metadata.json", payload)
    (artifact / "stdout.txt").write_text("", encoding="utf-8")
    (artifact / "stderr.txt").write_text("", encoding="utf-8")
    _write_json(artifact / "db-evidence.json", {})
    _write_json(artifact / "effects.json", {})
    return artifact


def _query_delegation(db_path: Path, delegation_id: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"available": False, "reason": "state.db absent"}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result: dict[str, Any] = {"available": True, "quick_check": quick_check}
        parent_session_id = None
        if "async_delegations" in tables:
            row = connection.execute(
                "SELECT delegation_id, state, dispatched_at, completed_at, updated_at, "
                "delivery_state, delivery_attempts, parent_session_id "
                "FROM async_delegations WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
            result["async_delegation"] = dict(row) if row else None
            parent_session_id = row["parent_session_id"] if row else None
        if parent_session_id and "sessions" in tables:
            rows = connection.execute(
                "SELECT id, parent_session_id, source, model, end_reason, "
                "tool_call_count, input_tokens, output_tokens, reasoning_tokens "
                "FROM sessions WHERE id=? OR parent_session_id=? ORDER BY started_at",
                (parent_session_id, parent_session_id),
            ).fetchall()
            result["sessions"] = [dict(row) for row in rows]
            session_ids = [row["id"] for row in rows]
            if session_ids and "session_model_usage" in tables:
                placeholders = ",".join("?" for _ in session_ids)
                usage = connection.execute(
                    "SELECT session_id, model, api_call_count, input_tokens, "
                    "output_tokens, reasoning_tokens, estimated_cost_usd, task "
                    f"FROM session_model_usage WHERE session_id IN ({placeholders}) "
                    "ORDER BY session_id, model, task",
                    session_ids,
                ).fetchall()
                result["session_model_usage"] = [dict(row) for row in usage]
        return result
    finally:
        connection.close()


def _tool_events(transcript: str) -> list[str]:
    return re.findall(r"\btool\s+\|\s+->\s+([A-Za-z0-9_.-]+)\(", transcript)


def require_isolated_hermes_home(hermes_home: Path) -> Path:
    """Accept only the dedicated E2E profile, never the live default profile."""

    resolved = hermes_home.expanduser().resolve(strict=True)
    expected = (Path.home() / ".hermes" / "profiles" / "hermes-agents-skills-test").resolve()
    if resolved != expected:
        raise ValueError(f"E2E evidence requires the dedicated profile: {expected}")
    return resolved


def capture_delegation(
    *,
    hermes_home: Path,
    delegation_id: str,
    artifact_root: Path,
    scenario_id: str,
    allowed: set[str],
    forbidden: set[str],
    model: str,
    provider: str,
) -> Path:
    hermes_home = require_isolated_hermes_home(hermes_home)
    reject_secrets(" ".join((delegation_id, scenario_id, model, provider)))
    source = hermes_home / "cache" / "delegation" / "live" / delegation_id
    transcript_path = source / "task-0.log"
    manifest_path = source / "manifest.json"
    if not transcript_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"delegation evidence not found: {delegation_id}")

    transcript = transcript_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reject_secrets(transcript)
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    reject_secrets(manifest_text)
    tools = _tool_events(transcript)
    seen = set(tools)
    allowed_seen = sorted(seen & allowed)
    forbidden_seen = sorted(seen & forbidden)
    status = "PASS" if allowed_seen and not forbidden_seen else "FAIL"

    artifact = create_artifact_bundle(
        artifact_root,
        scenario_id=scenario_id,
        metadata={
            "delegation_id": delegation_id,
            "provider": provider,
            "model": model,
            "reasoning": DEFAULT_REASONING,
            "max_calls": DEFAULT_MAX_CALLS,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "oracle": "tool trace + effects + read-only SQLite evidence",
        },
    )
    shutil.copy2(transcript_path, artifact / "transcript.log")
    _write_json(artifact / "manifest.json", manifest)
    db_evidence = _query_delegation(hermes_home / "state.db", delegation_id)
    _write_json(artifact / "db-evidence.json", db_evidence)
    effects = {
        "status": status,
        "tool_calls": tools,
        "required_allowed": sorted(allowed),
        "allowed_observed": allowed_seen,
        "forbidden": sorted(forbidden),
        "forbidden_observed": forbidden_seen,
        "forbidden_effect_absent": not forbidden_seen,
        "manifest_status": manifest.get("tasks", [{}])[0].get("status"),
    }
    _write_json(artifact / "effects.json", effects)
    (artifact / "stdout.txt").write_text(
        f"{scenario_id} {status}\nallowed_observed={','.join(allowed_seen)}\n"
        f"forbidden_observed={','.join(forbidden_seen)}\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise RuntimeError(f"{scenario_id} failed; evidence: {artifact}")
    return artifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture-delegation")
    capture.add_argument("--hermes-home", type=Path, required=True)
    capture.add_argument("--delegation-id", required=True)
    capture.add_argument("--artifact-root", type=Path, required=True)
    capture.add_argument("--scenario-id", required=True)
    capture.add_argument("--allowed", default="")
    capture.add_argument("--forbidden", default="")
    capture.add_argument("--model", default=DEFAULT_MODEL)
    capture.add_argument("--provider", default=DEFAULT_PROVIDER)

    status = sub.add_parser("status")
    status.add_argument("--artifact-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capture-delegation":
        artifact = capture_delegation(
            hermes_home=args.hermes_home,
            delegation_id=args.delegation_id,
            artifact_root=args.artifact_root,
            scenario_id=args.scenario_id,
            allowed=parse_csv_names(args.allowed),
            forbidden=parse_csv_names(args.forbidden),
            model=args.model,
            provider=args.provider,
        )
        print(artifact)
        return 0
    if args.command == "status":
        rows = sorted(args.artifact_root.glob("*/effects.json")) if args.artifact_root.exists() else []
        for path in rows:
            effects = json.loads(path.read_text(encoding="utf-8"))
            print(f"{path.parent.name}: {effects.get('status', 'UNKNOWN')}")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
