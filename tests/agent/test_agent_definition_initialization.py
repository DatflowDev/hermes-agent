from pathlib import Path
from unittest.mock import patch

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from run_agent import AIAgent


def _definition(identity: str = "replace") -> str:
    return (
        "---\n"
        "name: reviewer\n"
        "description: Review releases\n"
        f"identity: {identity}\n"
        "---\n"
        "You are the release reviewer.\n"
    )


def test_agent_initialization_pins_profile_catalog_and_projects_schema(tmp_path: Path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "reviewer.md").write_text(_definition())
    token = set_hermes_home_override(tmp_path)
    try:
        with (
            patch("run_agent.get_tool_definitions") as definitions,
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
        ):
            definitions.return_value = [
                {
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "description": "delegate",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "tasks": {
                                    "type": "array",
                                    "items": {"type": "object", "properties": {}},
                                }
                            },
                        },
                    },
                }
            ]
            agent = AIAgent(
                api_key="test-key",
                base_url="https://example.invalid/v1",
                provider="openai-compat",
                model="test/model",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
    finally:
        reset_hermes_home_override(token)

    assert agent._agent_catalog.get("reviewer") is not None
    function = agent.tools[0]["function"]
    assert function["parameters"]["properties"]["agent_name"]["enum"] == ["reviewer"]
    assert "You are the release reviewer" not in str(agent.tools)
    agent.close()
