from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.startup_skills import (
    StartupSkillError,
    render_startup_skills,
    resolve_startup_skills,
)


def _skill(root: Path, name: str, body: str, *, extra: str = "") -> Path:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: Test skill\n{extra}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_resolve_pins_exact_raw_bytes_without_preprocessing(tmp_path: Path) -> None:
    path = _skill(tmp_path, "grounded-citations", "Literal {{TOKEN}} and !`printf side-effect`")

    pins = resolve_startup_skills(tmp_path, ["grounded-citations"])

    assert len(pins) == 1
    assert pins[0].name == "grounded-citations"
    assert pins[0].relative_path == "grounded-citations/SKILL.md"
    assert pins[0].digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "{{TOKEN}}" in pins[0].content
    assert "!`printf side-effect`" in pins[0].content
    rendered = render_startup_skills(pins)
    assert "grounded-citations" in rendered
    assert pins[0].content in rendered


def test_disabled_policy_is_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _skill(tmp_path, "blocked-skill", "Blocked content")
    import agent.skill_utils as skill_utils

    monkeypatch.setattr(skill_utils, "get_disabled_skill_names", lambda: {"blocked-skill"})
    with pytest.raises(StartupSkillError, match="disabled"):
        resolve_startup_skills(tmp_path, ["blocked-skill"])

    def broken_policy():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(skill_utils, "get_disabled_skill_names", broken_policy)
    with pytest.raises(StartupSkillError, match="policy"):
        resolve_startup_skills(tmp_path, ["blocked-skill"])


def test_missing_ambiguous_and_symlinked_skills_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(StartupSkillError, match="unavailable"):
        resolve_startup_skills(tmp_path, ["missing"])

    _skill(tmp_path, "first", "One", extra="")
    duplicate = tmp_path / "skills" / "duplicate"
    duplicate.mkdir()
    (duplicate / "SKILL.md").write_text(
        "---\nname: first\ndescription: Duplicate\n---\nTwo\n",
        encoding="utf-8",
    )
    with pytest.raises(StartupSkillError, match="ambiguous"):
        resolve_startup_skills(tmp_path, ["first"])

    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: linked\ndescription: Linked\n---\nOutside\n")
    linked_dir = tmp_path / "skills" / "linked"
    linked_dir.mkdir()
    (linked_dir / "SKILL.md").symlink_to(outside)
    with pytest.raises(StartupSkillError, match="symlink"):
        resolve_startup_skills(tmp_path, ["linked"])


def test_startup_skills_reject_symlinked_profile_root(tmp_path: Path) -> None:
    real_profile = tmp_path / "real"
    _skill(real_profile, "safe", "Safe.")
    linked_profile = tmp_path / "linked-profile"
    linked_profile.symlink_to(real_profile, target_is_directory=True)

    with pytest.raises(StartupSkillError, match="profile root"):
        resolve_startup_skills(linked_profile, ["safe"])
