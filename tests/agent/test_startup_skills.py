from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agent.startup_skills import (
    MAX_STARTUP_SKILL_BYTES,
    MAX_STARTUP_SKILLS_TOTAL_BYTES,
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


def test_oversized_requested_skill_fails_closed(tmp_path: Path) -> None:
    path = _skill(tmp_path, "requested", "x" * MAX_STARTUP_SKILL_BYTES)
    assert path.stat().st_size > MAX_STARTUP_SKILL_BYTES

    with pytest.raises(StartupSkillError, match="size limit"):
        resolve_startup_skills(tmp_path, ["requested"])


def test_duplicate_requested_declarations_fail_before_discovery(tmp_path: Path) -> None:
    _skill(tmp_path, "requested", "Requested content")

    with pytest.raises(StartupSkillError, match="declarations are invalid"):
        resolve_startup_skills(tmp_path, ["requested", "requested"])


def test_malformed_requested_frontmatter_fails_closed(tmp_path: Path) -> None:
    directory = tmp_path / "skills" / "requested"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: [requested\ndescription: Broken\n---\nBody\n",
        encoding="utf-8",
    )

    with pytest.raises(StartupSkillError, match="frontmatter"):
        resolve_startup_skills(tmp_path, ["requested"])


def test_unrequested_invalid_content_does_not_block_resolution(tmp_path: Path) -> None:
    _skill(tmp_path, "requested", "Requested content")
    oversized = tmp_path / "skills" / "oversized"
    oversized.mkdir()
    (oversized / "SKILL.md").write_bytes(b"x" * (MAX_STARTUP_SKILL_BYTES + 1))
    malformed = tmp_path / "skills" / "malformed"
    malformed.mkdir()
    (malformed / "SKILL.md").write_bytes(
        b"---\nname: [malformed\n---\n\xff"
    )

    pins = resolve_startup_skills(tmp_path, ["requested"])

    assert [pin.name for pin in pins] == ["requested"]


def test_unrequested_disabled_skill_does_not_block_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skill(tmp_path, "requested", "Requested content")
    _skill(tmp_path, "disabled-unrelated", "Disabled unrelated content")
    import agent.skill_utils as skill_utils

    monkeypatch.setattr(
        skill_utils, "get_disabled_skill_names", lambda: {"disabled-unrelated"}
    )

    pins = resolve_startup_skills(tmp_path, ["requested"])

    assert [pin.name for pin in pins] == ["requested"]


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="POSIX mode semantics")
def test_unrequested_writable_skill_directory_does_not_block_resolution(
    tmp_path: Path,
) -> None:
    _skill(tmp_path, "requested", "Requested content")
    unrelated = _skill(tmp_path, "unrelated", "Unrelated content")
    unrelated.parent.chmod(0o777)

    pins = resolve_startup_skills(tmp_path, ["requested"])

    assert [pin.name for pin in pins] == ["requested"]


def test_requested_skill_total_content_limit_remains_enforced(tmp_path: Path) -> None:
    names = [f"requested-{index}" for index in range(5)]
    for name in names:
        _skill(tmp_path, name, "x" * (MAX_STARTUP_SKILL_BYTES - 128))

    with pytest.raises(StartupSkillError, match="total size limit"):
        resolve_startup_skills(tmp_path, names)


def test_discovery_entry_budget_counts_unrequested_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.startup_skills as startup_skills

    _skill(tmp_path, "requested", "Requested content")
    (tmp_path / "skills" / "unrelated-a").mkdir()
    (tmp_path / "skills" / "unrelated-b").mkdir()
    monkeypatch.setattr(startup_skills, "MAX_STARTUP_SKILL_ENTRIES", 2)

    with pytest.raises(StartupSkillError, match="entry budget"):
        resolve_startup_skills(tmp_path, ["requested"])


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="requires procfs")
def test_discovery_failure_closes_pending_directory_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.startup_skills as startup_skills

    _skill(tmp_path, "requested", "Requested content")
    for index in range(8):
        (tmp_path / "skills" / f"unrelated-{index}").mkdir()
    monkeypatch.setattr(startup_skills, "MAX_STARTUP_SKILL_ENTRIES", 2)
    descriptors_before = len(os.listdir("/proc/self/fd"))

    for _ in range(10):
        with pytest.raises(StartupSkillError, match="entry budget"):
            resolve_startup_skills(tmp_path, ["requested"])

    assert len(os.listdir("/proc/self/fd")) == descriptors_before


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="requires procfs")
def test_child_directory_fstat_failure_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.startup_skills as startup_skills

    _skill(tmp_path, "requested", "Requested content")
    original_fstat = startup_skills.os.fstat

    def fail_requested_directory_fstat(descriptor: int) -> os.stat_result:
        target = Path(f"/proc/self/fd/{descriptor}")
        try:
            if target.resolve() == tmp_path / "skills" / "requested":
                raise OSError("simulated child directory fstat failure")
        except FileNotFoundError:
            pass
        return original_fstat(descriptor)

    monkeypatch.setattr(startup_skills.os, "fstat", fail_requested_directory_fstat)
    descriptors_before = len(os.listdir("/proc/self/fd"))

    for _ in range(10):
        with pytest.raises(StartupSkillError, match="unavailable"):
            resolve_startup_skills(tmp_path, ["requested"])

    assert len(os.listdir("/proc/self/fd")) == descriptors_before


def test_frontmatter_probe_handles_short_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.startup_skills as startup_skills

    _skill(tmp_path, "requested", "Requested content")
    original_read = startup_skills.os.read

    def short_read(descriptor: int, size: int) -> bytes:
        return original_read(descriptor, min(size, 7))

    monkeypatch.setattr(startup_skills.os, "read", short_read)

    pins = resolve_startup_skills(tmp_path, ["requested"])

    assert [pin.name for pin in pins] == ["requested"]


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="POSIX mode semantics")
@pytest.mark.parametrize("target", ["profile", "skills", "ancestor", "file"])
def test_requested_skill_rejects_group_or_world_writable_authority_path(
    tmp_path: Path, target: str
) -> None:
    profile = tmp_path / "profile"
    path = _skill(profile, "requested", "Requested content")
    selected = {
        "profile": profile,
        "skills": profile / "skills",
        "ancestor": path.parent,
        "file": path,
    }[target]
    selected.chmod(0o777 if selected.is_dir() else 0o666)

    with pytest.raises(StartupSkillError, match="group/world writable"):
        resolve_startup_skills(profile, ["requested"])


def test_requested_skill_changed_during_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.startup_skills as startup_skills

    _skill(tmp_path, "requested", "Requested content")
    original_read_bounded = startup_skills._read_bounded
    authenticated_reads = 0

    def mismatched_second_read(descriptor: int, size: int) -> bytes:
        nonlocal authenticated_reads
        data = original_read_bounded(descriptor, size)
        if size == MAX_STARTUP_SKILL_BYTES + 1:
            authenticated_reads += 1
            if authenticated_reads == 3:
                return data + b"changed"
        return data

    monkeypatch.setattr(startup_skills, "_read_bounded", mismatched_second_read)

    with pytest.raises(StartupSkillError, match="changed during read"):
        resolve_startup_skills(tmp_path, ["requested"])


def test_requested_skill_rejects_hardlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\nname: requested\ndescription: Requested\n---\nBody\n",
        encoding="utf-8",
    )
    requested = tmp_path / "skills" / "requested"
    requested.mkdir(parents=True)
    (requested / "SKILL.md").hardlink_to(outside)

    with pytest.raises(StartupSkillError, match="singly-linked regular file"):
        resolve_startup_skills(tmp_path, ["requested"])


def test_requested_skill_rejects_invalid_utf8(tmp_path: Path) -> None:
    requested = tmp_path / "skills" / "requested"
    requested.mkdir(parents=True)
    (requested / "SKILL.md").write_bytes(
        b"---\nname: requested\ndescription: Requested\n---\n\xff"
    )

    with pytest.raises(StartupSkillError, match="valid UTF-8"):
        resolve_startup_skills(tmp_path, ["requested"])
