from types import SimpleNamespace

from agent.agent_definitions import discover_profile_agents
from hermes_cli.cli_commands_mixin import CLICommandsMixin


class _Stub(CLICommandsMixin):
    def __init__(self, agent):
        self.agent = agent


def test_agents_definitions_lists_pinned_catalog(capsys, tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review releases\nidentity: replace\n"
        "tools:\n  allow: [read_file]\nmcp:\n  allow: []\n---\nReview.\n"
    )
    stub = _Stub(SimpleNamespace(_agent_catalog=discover_profile_agents(tmp_path)))

    stub._handle_agents_command("/agents definitions")

    output = capsys.readouterr().out
    assert "reviewer" in output
    assert "Review releases" in output
    assert "Review." not in output


def test_agents_show_projects_tool_and_mcp_policy(capsys, tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review releases\nidentity: replace\n"
        "tools:\n  allow: [read_file]\nmcp:\n  allow: []\n---\nReview.\n"
    )
    stub = _Stub(SimpleNamespace(_agent_catalog=discover_profile_agents(tmp_path)))

    stub._handle_agents_command("/agents show reviewer")

    output = capsys.readouterr().out
    assert "Tools: read_file" in output
    assert "MCP: none" in output
