from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path

import pytest

from agent.agent_definitions import (
    MAX_AGENT_FILE_BYTES,
    MAX_AGENT_FILES,
    MAX_BODY_BYTES,
    MAX_DESCRIPTION_CHARS,
    AgentDefinitionError,
    discover_profile_agents,
    parse_agent_definition,
    reload_catalog_entry,
)


def _definition(
    *,
    name: str = "researcher",
    description: str = "Find and verify sources",
    identity: str | None = "profile",
    extra: str = "",
    body: str = "Work carefully and cite evidence.",
) -> bytes:
    identity_line = "" if identity is None else f"identity: {identity}\n"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{identity_line}"
        f"{extra}"
        "---\n"
        f"{body}\n"
    ).encode()


def _assert_error(raw: bytes, code: str) -> None:
    with pytest.raises(AgentDefinitionError) as exc:
        parse_agent_definition(raw, Path("research/researcher.md"))
    assert exc.value.code == code


def test_parse_minimal_profile_definition() -> None:
    definition = parse_agent_definition(_definition(), Path("research/researcher.md"))
    assert definition.name == "researcher"
    assert definition.description == "Find and verify sources"
    assert definition.identity == "profile"
    assert definition.instructions == "Work carefully and cite evidence."
    assert definition.provider is None
    assert definition.model is None
    assert definition.fallbacks is None
    assert definition.relative_path == "research/researcher.md"
    assert len(definition.full_digest) == 64


def test_parse_replace_definition_with_route() -> None:
    definition = parse_agent_definition(
        _definition(
            identity="replace",
            extra=(
                "provider: custom\n"
                "model: cx/gpt-5.4-mini-high\n"
                "fallbacks:\n"
                "  - provider: openrouter\n"
                "    model: openai/gpt-5-mini\n"
            ),
        ),
        Path("researcher.md"),
    )
    assert definition.identity == "replace"
    assert definition.provider == "custom"
    assert definition.model == "cx/gpt-5.4-mini-high"
    assert [(route.provider, route.model) for route in definition.fallbacks or ()] == [
        ("openrouter", "openai/gpt-5-mini")
    ]


@pytest.mark.parametrize("identity", [None, "inherit", "", "PROFILE"])
def test_identity_is_required_closed_enum(identity: str | None) -> None:
    _assert_error(_definition(identity=identity), "AGENT_DEFINITION_INVALID")


@pytest.mark.parametrize(
    "extra",
    [
        "tools: []\n",
        "endpoint: https://example.invalid\n",
        "api_key: secret\n",
        "hooks: {}\n",
        "provider: &p custom\nmodel: *p\n",
        "description: !unsafe value\n",
        "<<: {provider: custom}\n",
        "provider: ${SECRET_PROVIDER}\nmodel: x\n",
    ],
)
def test_unsupported_or_unsafe_yaml_is_rejected(extra: str) -> None:
    _assert_error(_definition(extra=extra), "AGENT_FIELD_UNSUPPORTED")


def test_duplicate_yaml_keys_are_rejected() -> None:
    _assert_error(_definition(extra="name: duplicate\n"), "AGENT_DEFINITION_INVALID")


def test_unhashable_yaml_key_is_wrapped_as_definition_error() -> None:
    _assert_error(
        b"---\n? [one, two]\n: value\nname: x\ndescription: x\nidentity: replace\n---\nbody\n",
        "AGENT_DEFINITION_INVALID",
    )


def test_markdown_body_may_start_with_thematic_break() -> None:
    definition = parse_agent_definition(
        _definition(body="---\nThis is Markdown, not a second frontmatter document."),
        Path("researcher.md"),
    )
    assert definition.instructions.startswith("---\n")


@pytest.mark.parametrize("name", ["Researcher", "a/b", "a b", "-agent", "équipe"])
def test_name_must_already_be_canonical(name: str) -> None:
    _assert_error(_definition(name=name), "AGENT_DEFINITION_INVALID")


def test_description_and_body_limits_are_hard_failures() -> None:
    parse_agent_definition(
        _definition(description="d" * MAX_DESCRIPTION_CHARS, body="b" * MAX_BODY_BYTES),
        Path("limit.md"),
    )
    _assert_error(
        _definition(description="d" * (MAX_DESCRIPTION_CHARS + 1)),
        "AGENT_DEFINITION_INVALID",
    )
    _assert_error(
        _definition(body="b" * (MAX_BODY_BYTES + 1)),
        "AGENT_DEFINITION_INVALID",
    )


def test_invalid_utf8_nul_and_oversize_file_are_rejected() -> None:
    _assert_error(b"---\nname: x\n\xff", "AGENT_DEFINITION_INVALID")
    _assert_error(_definition(body="bad\x00body"), "AGENT_DEFINITION_INVALID")
    _assert_error(b"x" * (MAX_AGENT_FILE_BYTES + 1), "AGENT_DEFINITION_INVALID")


def test_discovery_is_deterministic_profile_scoped_and_bounded(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    (agents / "z").mkdir(parents=True)
    (agents / "a").mkdir()
    (agents / "z" / "second.md").write_bytes(_definition(name="second"))
    (agents / "a" / "first.md").write_bytes(_definition(name="first"))
    (tmp_path.parent / "outside.md").write_bytes(_definition(name="outside"))

    catalog = discover_profile_agents(tmp_path)
    assert [entry.definition.name for entry in catalog.entries] == ["first", "second"]
    assert catalog.get("first").definition.relative_path == "a/first.md"
    assert catalog.get("outside") is None


def test_discovery_rejects_name_collisions(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "one.md").write_bytes(_definition(name="same"))
    (agents / "two.md").write_bytes(_definition(name="same"))
    with pytest.raises(AgentDefinitionError) as exc:
        discover_profile_agents(tmp_path)
    assert exc.value.code == "AGENT_DEFINITION_COLLISION"


def test_discovery_rejects_symlink_and_symlinked_parent(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_bytes(_definition(name="outside"))
    (agents / "linked.md").symlink_to(outside)
    with pytest.raises(AgentDefinitionError) as exc:
        discover_profile_agents(tmp_path)
    assert exc.value.code == "SECURE_AGENT_LOAD_UNAVAILABLE"

    (agents / "linked.md").unlink()
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "nested.md").write_bytes(_definition(name="nested"))
    (agents / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(AgentDefinitionError) as exc:
        discover_profile_agents(tmp_path)
    assert exc.value.code == "SECURE_AGENT_LOAD_UNAVAILABLE"


def test_discovery_rejects_hardlinked_definition(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_bytes(_definition(name="outside"))
    (agents / "linked.md").hardlink_to(outside)

    with pytest.raises(AgentDefinitionError) as exc:
        discover_profile_agents(tmp_path)

    assert exc.value.code == "SECURE_AGENT_LOAD_UNAVAILABLE"


def test_discovery_rejects_catalog_file_limit(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    for index in range(MAX_AGENT_FILES + 1):
        (agents / f"a{index}.md").write_bytes(_definition(name=f"a{index}"))
    with pytest.raises(AgentDefinitionError) as exc:
        discover_profile_agents(tmp_path)
    assert exc.value.code == "AGENT_DISCOVERY_LIMIT"


def test_persisted_catalog_snapshot_survives_source_change(tmp_path: Path) -> None:
    from agent.agent_definitions import restore_agent_catalog, snapshot_agent_catalog

    agents = tmp_path / "agents"
    agents.mkdir()
    source = agents / "reviewer.md"
    source.write_bytes(_definition(name="reviewer", body="Original identity"))
    catalog = discover_profile_agents(tmp_path)
    snapshot = snapshot_agent_catalog(catalog)

    source.write_bytes(_definition(name="reviewer", body="Changed identity"))
    restored = restore_agent_catalog(snapshot, tmp_path)

    assert restored.revision == catalog.revision
    assert restored.get("reviewer").definition.instructions == "Original identity"
    assert reload_catalog_entry(restored.get("reviewer")).instructions == "Original identity"


def test_persisted_catalog_preserves_explicit_empty_fallbacks(tmp_path: Path) -> None:
    from agent.agent_definitions import restore_agent_catalog, snapshot_agent_catalog

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_bytes(
        _definition(name="reviewer", identity="replace", extra="fallbacks: []\n")
    )

    catalog = discover_profile_agents(tmp_path)
    restored = restore_agent_catalog(snapshot_agent_catalog(catalog), tmp_path)

    assert catalog.get("reviewer").definition.fallbacks == ()
    assert restored.get("reviewer").definition.fallbacks == ()


def test_persisted_catalog_rejects_tampered_definition_fields(tmp_path: Path) -> None:
    from agent.agent_definitions import restore_agent_catalog, snapshot_agent_catalog

    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_bytes(_definition(name="reviewer", identity="replace"))
    snapshot = snapshot_agent_catalog(discover_profile_agents(tmp_path))
    snapshot["definitions"][0]["instructions"] = "TAMPERED"
    payload = {
        key: value
        for key, value in snapshot["definitions"][0].items()
        if key != "snapshot_digest"
    }
    snapshot["definitions"][0]["snapshot_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    with pytest.raises(AgentDefinitionError) as exc:
        restore_agent_catalog(snapshot, tmp_path)

    assert exc.value.code == "STALE_AGENT_DEFINITION"


def test_reload_rejects_mutation_before_spawn(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    path = agents / "researcher.md"
    path.write_bytes(_definition())
    entry = discover_profile_agents(tmp_path).get("researcher")
    assert entry is not None

    path.write_bytes(_definition(body="Changed after discovery"))
    with pytest.raises(AgentDefinitionError) as exc:
        reload_catalog_entry(entry)
    assert exc.value.code == "STALE_AGENT_DEFINITION"


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard-link semantics")
def test_reload_rejects_inode_replacement_even_with_same_bytes(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    path = agents / "researcher.md"
    raw = _definition()
    path.write_bytes(raw)
    entry = discover_profile_agents(tmp_path).get("researcher")
    assert entry is not None

    replacement = agents / "replacement.md"
    replacement.write_bytes(raw)
    os.replace(replacement, path)
    with pytest.raises(AgentDefinitionError) as exc:
        reload_catalog_entry(entry)
    assert exc.value.code == "STALE_AGENT_DEFINITION"
