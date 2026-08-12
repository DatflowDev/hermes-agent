# Session E2E tests

This directory contains executable end-to-end session harnesses, not pytest tests.

Rules:

- Scripts must not use `test_*.py` or `*_test.py` names.
- Run against a dedicated Hermes profile, never the default profile.
- Never include secrets, credentials, authorization headers, `.env`, or profile configuration in artifacts.
- Never contact, inspect, stop, or restart 9router or any service.
- Every live scenario pins provider, model, reasoning, turn budget, timeout, profile, and expected evidence.
- Prefer zero-LLM deterministic checks. Live scenarios use `cx/gpt-5.6-terra-medium`; Luna is a separately approved fallback only.
- A model's prose is not a PASS oracle. Verify tool traces, filesystem effects, events, and SQLite state.
- Artifacts go under `session-test-artifacts/`, which is ignored by Git.
- `stop` may terminate only processes started and recorded by this harness.
