#!/usr/bin/env python3
"""Self-checks for the Hermes Markdown-agent E2E session runner."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "run_markdown_agent_e2e.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("markdown_agent_e2e", RUNNER)
    if spec is None or spec.loader is None:
        raise AssertionError("runner module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    runner = load_runner()

    assert runner.DEFAULT_MODEL == "cx/gpt-5.6-terra-medium"
    assert runner.DEFAULT_PROVIDER == "omniroute-gpt"
    assert runner.DEFAULT_REASONING == "low"
    assert runner.DEFAULT_MAX_CALLS == 4
    assert runner.DEFAULT_TIMEOUT_SECONDS == 90

    safe = "Read /tmp/sentinel and report PASS"
    runner.reject_secrets(safe)
    for unsafe in (
        "Authorization: Bearer abc123",
        "api_key=secret",
        "password: hunter2",
        "token=abc",
        "-----BEGIN PRIVATE KEY-----",
    ):
        try:
            runner.reject_secrets(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"secret guard accepted: {unsafe!r}")

    assert runner.parse_csv_names("read_file, search_files") == {"read_file", "search_files"}
    assert runner.parse_csv_names("") == set()

    with tempfile.TemporaryDirectory(prefix="hermes-e2e-self-check-") as tmp:
        root = Path(tmp)
        artifact = runner.create_artifact_bundle(
            root,
            scenario_id="AUTH-01",
            metadata={"model": runner.DEFAULT_MODEL, "provider": runner.DEFAULT_PROVIDER},
        )
        metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["scenario_id"] == "AUTH-01"
        assert metadata["model"] == runner.DEFAULT_MODEL
        assert (artifact / "stdout.txt").exists()
        assert (artifact / "stderr.txt").exists()
        assert (artifact / "db-evidence.json").exists()
        assert (artifact / "effects.json").exists()

    print("SELF-CHECK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
