from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.agent_definitions import discover_profile_agents
from agent.system_prompt import build_system_prompt
from tools.delegate_tool import _build_child_agent, _run_single_child


def _write_definition(root: Path, *, identity: str, body: str) -> None:
    agents = root / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "reviewer.md").write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review releases\n"
        f"identity: {identity}\n"
        "---\n"
        f"{body}\n"
    )


def _parent(catalog):
    parent = MagicMock()
    parent._agent_catalog = catalog
    parent._delegate_depth = 0
    parent._active_children = []
    parent.enabled_toolsets = ["terminal", "file", "delegation"]
    parent.disabled_toolsets = []
    parent.base_url = "https://example.invalid/v1"
    parent.api_key = "test"
    parent.provider = "openai-compat"
    parent.api_mode = "chat_completions"
    parent.model = "parent-model"
    parent.platform = "cli"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent._session_db = None
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    return parent


def _prompt_agent(**overrides):
    values = dict(
        identity_override=None,
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="child-model",
        provider="openai-compat",
        platform="subagent",
        pass_session_id=False,
        session_id="child",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_replace_definition_end_to_end_uses_stable_identity_and_host_layers(tmp_path):
    identity = "You are the release reviewer."
    _write_definition(tmp_path, identity="replace", body=identity)
    definition = discover_profile_agents(tmp_path).entries[0].definition
    parent = _parent(discover_profile_agents(tmp_path))

    with patch("run_agent.AIAgent") as agent_cls:
        child = MagicMock()
        agent_cls.return_value = child
        _build_child_agent(
            task_index=0,
            goal="Review the release",
            context="Use the changelog",
            toolsets=None,
            model=None,
            max_iterations=10,
            parent_agent=parent,
            task_count=1,
            agent_definition=definition,
        )
        kwargs = agent_cls.call_args.kwargs

    prompt_agent = _prompt_agent(identity_override=kwargs["identity_override"])
    with (
        patch("run_agent.load_soul_md", return_value="PROFILE SOUL"),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        prompt = build_system_prompt(prompt_agent)

    assert prompt.startswith(identity + "\n\n")
    assert "PROFILE SOUL" not in prompt
    assert "Hermes Agent" in prompt
    assert identity not in kwargs["ephemeral_system_prompt"]


def test_profile_definition_end_to_end_uses_cached_additive_system_message(tmp_path):
    body = "Always verify the release evidence."
    _write_definition(tmp_path, identity="profile", body=body)
    catalog = discover_profile_agents(tmp_path)
    definition = catalog.entries[0].definition
    parent = _parent(catalog)
    captured = {}

    with patch("run_agent.AIAgent") as agent_cls:
        child = MagicMock()
        child.run_conversation.side_effect = lambda **kwargs: captured.update(kwargs) or {
            "final_response": "ok",
            "completed": True,
            "api_calls": 1,
        }
        agent_cls.return_value = child
        built = _build_child_agent(
            task_index=0,
            goal="Review",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            parent_agent=parent,
            task_count=1,
            agent_definition=definition,
        )
        _run_single_child(0, "Review", built, parent)
        kwargs = agent_cls.call_args.kwargs

    assert captured["system_message"] == body
    assert body not in kwargs["ephemeral_system_prompt"]
    assert kwargs["load_soul_identity"] is True
    assert kwargs["identity_override"] is None


def test_stale_definition_fails_before_dispatch(tmp_path):
    _write_definition(tmp_path, identity="replace", body="Original identity")
    catalog = discover_profile_agents(tmp_path)
    entry = catalog.entries[0]
    (tmp_path / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review releases\nidentity: replace\n---\nChanged identity\n"
    )

    from agent.agent_definitions import AgentDefinitionError, reload_catalog_entry

    try:
        reload_catalog_entry(entry)
    except AgentDefinitionError as exc:
        assert exc.code == "STALE_AGENT_DEFINITION"
    else:
        raise AssertionError("stale definition unexpectedly launched")
