"""Deterministic, side-effect-free startup skills for profile agents."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NoReturn

import yaml

MAX_STARTUP_SKILLS = 16
MAX_STARTUP_SKILL_BYTES = 64 * 1024
MAX_STARTUP_SKILLS_TOTAL_BYTES = 256 * 1024
MAX_STARTUP_SKILL_ENTRIES = 2048


class StartupSkillError(ValueError):
    pass


@dataclass(frozen=True)
class StartupSkillPin:
    name: str
    relative_path: str
    digest: str
    content: str


def _fail(message: str) -> NoReturn:
    raise StartupSkillError(message)


def _require_owned_restrictive(info: os.stat_result, label: str) -> None:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and info.st_uid != geteuid():
        _fail(f"{label} is not owned by the current user")
    if info.st_mode & 0o022:
        _fail(f"{label} is group/world writable")


def _read_regular_file_at(parent_fd: int, filename: str) -> bytes:
    """Read one file relative to an authenticated directory descriptor."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=parent_fd)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _fail("startup skill must be a singly-linked regular file")
            _require_owned_restrictive(before, "startup skill")
            data = os.read(descriptor, MAX_STARTUP_SKILL_BYTES + 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
            second_read = os.read(descriptor, MAX_STARTUP_SKILL_BYTES + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except StartupSkillError:
        raise
    except OSError:
        _fail("cannot securely read startup skill")
    if len(data) > MAX_STARTUP_SKILL_BYTES:
        _fail("startup skill exceeds its size limit")
    if data != second_read:
        _fail("startup skill changed during read")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        _fail("startup skill changed during read")
    return data


def _frontmatter_name(data: bytes, fallback: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail("startup skill is not valid UTF-8")
    if not text.startswith("---\n"):
        return fallback
    closing = text.find("\n---\n", 4)
    if closing < 0:
        _fail("startup skill frontmatter is not closed")
    try:
        metadata = yaml.safe_load(text[4:closing]) or {}
    except yaml.YAMLError:
        _fail("startup skill frontmatter is invalid")
    name = metadata.get("name", fallback) if isinstance(metadata, dict) else fallback
    if not isinstance(name, str) or not name:
        _fail("startup skill name is invalid")
    return name


def resolve_startup_skills(profile_root: Path | str, names: Iterable[str]) -> tuple[StartupSkillPin, ...]:
    """Resolve profile-local SKILL.md files without setup or preprocessing side effects."""
    requested = tuple(names)
    if len(requested) > MAX_STARTUP_SKILLS or len(set(requested)) != len(requested):
        _fail("startup skill declarations are invalid")
    if not requested:
        return ()
    try:
        from agent.skill_utils import get_disabled_skill_names

        disabled = set(get_disabled_skill_names())
    except Exception:
        _fail("startup skill disabled policy is unavailable")

    wanted = set(requested)
    candidates: dict[str, list[StartupSkillPin]] = {name: [] for name in requested}
    visited = 0
    total_bytes = 0
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    profile = Path(profile_root).absolute()
    try:
        profile_fd = os.open(profile, directory_flags)
    except OSError:
        _fail("cannot securely open the profile root")
    root_fd = -1
    try:
        profile_info = os.fstat(profile_fd)
        _require_owned_restrictive(profile_info, "profile root")
        try:
            root_fd = os.open("skills", directory_flags, dir_fd=profile_fd)
        except OSError:
            _fail("startup skill is unavailable: cannot securely open the profile skills root")
        root_info = os.fstat(root_fd)
        _require_owned_restrictive(root_info, "profile skills root")
        stack: list[tuple[int, tuple[str, ...]]] = [(root_fd, ())]
        root_fd = -1  # ownership moved to stack
        while stack:
            directory_fd, relative_parts = stack.pop()
            try:
                names_in_dir = sorted(os.listdir(directory_fd))
                for entry_name in names_in_dir:
                    visited += 1
                    if visited > MAX_STARTUP_SKILL_ENTRIES:
                        _fail("startup skill discovery entry budget exceeded")
                    try:
                        info = os.stat(
                            entry_name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                    except OSError:
                        _fail("cannot securely inspect startup skill path")
                    if stat.S_ISLNK(info.st_mode):
                        _fail("symlinks are not allowed below the profile skills root")
                    if stat.S_ISDIR(info.st_mode):
                        _require_owned_restrictive(info, "startup skill ancestor")
                        try:
                            child_fd = os.open(entry_name, directory_flags, dir_fd=directory_fd)
                        except OSError:
                            _fail("cannot securely open startup skill ancestor")
                        opened_child = os.fstat(child_fd)
                        if (opened_child.st_dev, opened_child.st_ino) != (info.st_dev, info.st_ino):
                            os.close(child_fd)
                            _fail("startup skill ancestor changed during discovery")
                        stack.append((child_fd, (*relative_parts, entry_name)))
                        continue
                    if entry_name != "SKILL.md":
                        continue
                    data = _read_regular_file_at(directory_fd, entry_name)
                    fallback = relative_parts[-1] if relative_parts else "skills"
                    resolved_name = _frontmatter_name(data, fallback)
                    if resolved_name not in wanted:
                        continue
                    if resolved_name in disabled:
                        _fail(f"startup skill '{resolved_name}' is disabled")
                    total_bytes += len(data)
                    if total_bytes > MAX_STARTUP_SKILLS_TOTAL_BYTES:
                        _fail("startup skills exceed their total size limit")
                    candidates[resolved_name].append(
                        StartupSkillPin(
                            name=resolved_name,
                            relative_path=Path(*relative_parts, entry_name).as_posix(),
                            digest=hashlib.sha256(data).hexdigest(),
                            content=data.decode("utf-8"),
                        )
                    )
            finally:
                os.close(directory_fd)
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(profile_fd)
    pins: list[StartupSkillPin] = []
    for name in requested:
        matches = candidates[name]
        if not matches:
            _fail(f"startup skill '{name}' is unavailable")
        if len(matches) != 1:
            _fail(f"startup skill '{name}' is ambiguous")
        pins.append(matches[0])
    return tuple(pins)


def render_startup_skills(pins: Iterable[StartupSkillPin]) -> str:
    """Render already-authenticated skill content without performing I/O."""

    return "\n\n".join(
        f'[Startup skill "{pin.name}" — active guidance for this delegated agent]\n\n'
        f"{pin.content}"
        for pin in pins
    )


__all__ = [
    "MAX_STARTUP_SKILLS",
    "StartupSkillError",
    "StartupSkillPin",
    "render_startup_skills",
    "resolve_startup_skills",
]
