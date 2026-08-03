"""Strict, bounded profile-scoped Markdown agent definitions.

Definitions are trusted operator configuration, not executable extensions.  This
module deliberately does not reuse the permissive skill frontmatter parser.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import yaml

MAX_AGENT_FILE_BYTES = 32 * 1024
MAX_AGENT_FILES = 64
MAX_AGENT_ENTRIES = 512
MAX_AGENT_TOTAL_BYTES = 1024 * 1024
MAX_AGENT_DEPTH = 4
MAX_RELATIVE_PATH_BYTES = 240
MAX_FRONTMATTER_BYTES = 8 * 1024
MAX_FRONTMATTER_LINES = 64
MAX_DESCRIPTION_CHARS = 160
MAX_BODY_BYTES = 24 * 1024
MAX_ROUTE_IDENTIFIER_CHARS = 128
MAX_FALLBACKS = 4
MAX_TOOL_IDENTIFIERS = 128
MAX_MCP_SERVERS = 32
MAX_NAME_CHARS = 64

_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_INTERPOLATION_RE = re.compile(r"\$\{|\{\{")
_ALLOWED_FIELDS = frozenset(
    {"name", "description", "identity", "provider", "model", "fallbacks", "tools", "mcp"}
)
_ALLOWED_ROUTE_FIELDS = frozenset({"provider", "model"})
_ALLOWED_RESTRICTION_FIELDS = frozenset({"allow"})
_SNAPSHOT_KEY_FILE = ".agent-catalog-signing-key"
_SNAPSHOT_KEY_BYTES = 32
_SNAPSHOT_KEY_OPEN_RETRIES = 8


def _require_owned_restrictive(info: os.stat_result, label: str) -> None:
    """Require system-authority paths to be owned by this process and not writable by peers."""

    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and info.st_uid != geteuid():
        _fail(f"{label} is not owned by the current user", "SECURE_AGENT_LOAD_UNAVAILABLE")
    if info.st_mode & 0o022:
        _fail(f"{label} is group/world writable", "SECURE_AGENT_LOAD_UNAVAILABLE")


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("short write while publishing agent catalog key")
        offset += written


class AgentDefinitionError(ValueError):
    """A safe, stable failure raised while loading an agent definition."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _catalog_snapshot_key(profile_root: Path, *, create: bool) -> bytes:
    """Read the profile-local key used to authenticate persisted catalogs."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(profile_root, directory_flags)
    except OSError as exc:
        raise AgentDefinitionError("SECURE_AGENT_LOAD_UNAVAILABLE", "agent catalog signing root is unavailable") from exc
    try:
        _require_owned_restrictive(os.fstat(root_fd), "agent catalog signing root")
        try:
            fd = os.open(_SNAPSHOT_KEY_FILE, file_flags, dir_fd=root_fd)
        except FileNotFoundError:
            if not create:
                _fail("stored agent catalog signing key is unavailable", "STALE_AGENT_DEFINITION")
            temporary_name = f".{_SNAPSHOT_KEY_FILE}.{secrets.token_hex(8)}.tmp"
            try:
                temporary_fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_fd,
                )
                key = secrets.token_bytes(_SNAPSHOT_KEY_BYTES)
                try:
                    _write_all(temporary_fd, key)
                    os.fsync(temporary_fd)
                finally:
                    os.close(temporary_fd)
                try:
                    os.link(
                        temporary_name,
                        _SNAPSHOT_KEY_FILE,
                        src_dir_fd=root_fd,
                        dst_dir_fd=root_fd,
                        follow_symlinks=False,
                    )
                    os.fsync(root_fd)
                except FileExistsError:
                    pass
            finally:
                try:
                    os.unlink(temporary_name, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            fd = os.open(_SNAPSHOT_KEY_FILE, file_flags, dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            # A concurrent first creator publishes with link(2) and immediately
            # removes its private temporary name. Retry that narrow transient;
            # persistent hard links still fail closed after the bounded wait.
            for _ in range(_SNAPSHOT_KEY_OPEN_RETRIES):
                if info.st_nlink == 1 or info.st_nlink != 2:
                    break
                import time

                time.sleep(0.001)
                info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                _fail("agent catalog signing key is insecure", "SECURE_AGENT_LOAD_UNAVAILABLE")
            _require_owned_restrictive(info, "agent catalog signing key")
            key = os.read(fd, _SNAPSHOT_KEY_BYTES + 1)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    if len(key) != _SNAPSHOT_KEY_BYTES:
        _fail("agent catalog signing key is invalid", "SECURE_AGENT_LOAD_UNAVAILABLE")
    return key


def _snapshot_mac(payload: dict[str, Any], key: bytes) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AgentFallbackRoute:
    provider: str
    model: str


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    identity: str
    instructions: str
    provider: str | None
    model: str | None
    fallbacks: tuple[AgentFallbackRoute, ...] | None
    tools_allow: tuple[str, ...] | None
    mcp_allow: tuple[str, ...] | None
    relative_path: str
    full_digest: str


@dataclass(frozen=True)
class AgentCatalogEntry:
    definition: AgentDefinition
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    agents_root_device: int
    agents_root_inode: int
    persisted: bool = False

    @property
    def definition_id(self) -> str:
        """Opaque stable id for this exact profile-relative definition revision."""
        material = (
            f"{self.definition.relative_path}\0{self.definition.full_digest}"
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class AgentCatalog:
    profile_root: Path
    agents_root: Path
    entries: tuple[AgentCatalogEntry, ...]
    revision: str

    def get(self, name: str) -> AgentCatalogEntry | None:
        for entry in self.entries:
            if entry.definition.name == name:
                return entry
        return None

    def get_by_id(self, definition_id: str) -> AgentCatalogEntry | None:
        for entry in self.entries:
            if entry.definition_id == definition_id:
                return entry
        return None

    def project(self) -> dict[str, Any]:
        """Return the single allowlisted projection consumed by every UI."""
        return {
            "version": 2,
            "revision": self.revision,
            "definitions": [
                {
                    "definition_id": entry.definition_id,
                    "name": entry.definition.name,
                    "description": entry.definition.description,
                    "identity": entry.definition.identity,
                    "provider": entry.definition.provider,
                    "model": entry.definition.model,
                    "fallback_count": len(entry.definition.fallbacks or ()),
                    "tools_allow": (
                        None
                        if entry.definition.tools_allow is None
                        else list(entry.definition.tools_allow)
                    ),
                    "mcp_allow": (
                        None
                        if entry.definition.mcp_allow is None
                        else list(entry.definition.mcp_allow)
                    ),
                    "relative_path": entry.definition.relative_path,
                    "digest": entry.definition.full_digest,
                }
                for entry in self.entries
            ],
        }


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _fail(message: str, code: str = "AGENT_DEFINITION_INVALID") -> NoReturn:
    raise AgentDefinitionError(code, message)


def _bounded_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ROUTE_IDENTIFIER_CHARS:
        _fail(f"{field} must be a non-empty bounded string")
    if _INTERPOLATION_RE.search(value):
        _fail(
            f"{field} contains unsupported interpolation",
            "AGENT_FIELD_UNSUPPORTED",
        )
    if any(ord(char) < 0x20 for char in value):
        _fail(f"{field} contains control characters")
    if value == "inherit":
        _fail(
            f"{field} cannot use the reserved inherit sentinel; omit the field to inherit",
            "AGENT_ROUTE_INVALID",
        )
    return value


def _parse_allowlist(
    value: Any,
    *,
    field: str,
    limit: int,
    error_code: str,
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail(f"{field} must be a mapping containing allow", error_code)
    unknown = set(value) - _ALLOWED_RESTRICTION_FIELDS
    if unknown:
        _fail(f"unsupported {field} field: {sorted(unknown)[0]}", "AGENT_FIELD_UNSUPPORTED")
    raw = value.get("allow")
    if not isinstance(raw, list) or len(raw) > limit:
        _fail(f"{field}.allow must be a bounded list", error_code)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        identifier = _bounded_identifier(item, f"{field}.allow[{index}]")
        if identifier in seen:
            _fail(f"{field}.allow entries must be unique", error_code)
        seen.add(identifier)
        result.append(identifier)
    return tuple(result)


def _split_frontmatter(raw: bytes) -> tuple[bytes, bytes]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    normalized = raw.replace(b"\r\n", b"\n")
    if not normalized.startswith(b"---\n"):
        _fail("agent definition requires YAML frontmatter")
    closing = normalized.find(b"\n---\n", 4)
    if closing < 0:
        _fail("agent definition frontmatter is not closed")
    frontmatter = normalized[4:closing]
    body = normalized[closing + 5 :]
    if len(frontmatter) > MAX_FRONTMATTER_BYTES or frontmatter.count(b"\n") + 1 > MAX_FRONTMATTER_LINES:
        _fail("agent frontmatter exceeds its limit")
    return frontmatter, body


def parse_agent_definition(raw_bytes: bytes, relative_path: Path | str) -> AgentDefinition:
    """Parse exact definition bytes with a closed schema and hard limits."""

    if not isinstance(raw_bytes, bytes) or len(raw_bytes) > MAX_AGENT_FILE_BYTES:
        _fail("agent definition exceeds its file-size limit")
    if b"\x00" in raw_bytes:
        _fail("agent definition contains NUL")
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _fail("agent definition is not valid UTF-8")

    frontmatter_bytes, body_bytes = _split_frontmatter(raw_bytes)
    if re.search(rb"(?m)(?:^|[\s\[\]{},])(?:&|\*)[A-Za-z0-9_-]+", frontmatter_bytes) or any(
        token in frontmatter_bytes for token in (b"!!", b"!unsafe", b"<<:")
    ):
        _fail("agent definition uses unsupported YAML features", "AGENT_FIELD_UNSUPPORTED")
    try:
        metadata = yaml.load(frontmatter_bytes.decode("utf-8"), Loader=_StrictLoader)
    except (yaml.YAMLError, RecursionError, TypeError, ValueError) as exc:
        _fail(f"invalid agent frontmatter: {exc}")
    if not isinstance(metadata, dict):
        _fail("agent frontmatter must be a mapping")
    if not all(isinstance(key, str) for key in metadata):
        _fail("agent frontmatter keys must be strings")
    unknown = sorted(set(metadata) - _ALLOWED_FIELDS)
    if unknown:
        _fail(f"unsupported agent field: {unknown[0]}", "AGENT_FIELD_UNSUPPORTED")

    name = metadata.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        _fail("name must be canonical lowercase ASCII with hyphens")
    description = metadata.get("description")
    if (
        not isinstance(description, str)
        or not description.strip()
        or description != description.strip()
        or len(description) > MAX_DESCRIPTION_CHARS
        or any(ord(char) < 0x20 for char in description)
    ):
        _fail("description must be non-empty bounded plain text")
    identity = metadata.get("identity")
    if identity not in {"profile", "replace"}:
        _fail("identity must be exactly profile or replace")

    provider_value = metadata.get("provider")
    model_value = metadata.get("model")
    provider = None if provider_value is None else _bounded_identifier(provider_value, "provider")
    model = None if model_value is None else _bounded_identifier(model_value, "model")
    if provider is not None and model is None:
        _fail("provider requires an explicit model", "AGENT_ROUTE_INCOMPLETE")

    fallbacks_value = metadata.get("fallbacks")
    fallbacks: tuple[AgentFallbackRoute, ...] | None
    if fallbacks_value is None:
        fallbacks = None
    else:
        if not isinstance(fallbacks_value, list) or len(fallbacks_value) > MAX_FALLBACKS:
            _fail("fallbacks must be a bounded list", "AGENT_FALLBACK_INVALID")
        parsed_routes: list[AgentFallbackRoute] = []
        seen_routes: set[tuple[str, str]] = set()
        for route in fallbacks_value:
            if not isinstance(route, dict) or set(route) != _ALLOWED_ROUTE_FIELDS:
                _fail("fallback route must contain only provider and model", "AGENT_FALLBACK_INVALID")
            parsed = AgentFallbackRoute(
                provider=_bounded_identifier(route["provider"], "fallback.provider"),
                model=_bounded_identifier(route["model"], "fallback.model"),
            )
            key = (parsed.provider, parsed.model)
            if key in seen_routes or (provider is not None and model is not None and key == (provider, model)):
                _fail("fallback routes must be unique and distinct from the primary route", "AGENT_FALLBACK_INVALID")
            seen_routes.add(key)
            parsed_routes.append(parsed)
        fallbacks = tuple(parsed_routes)

    tools_allow = _parse_allowlist(
        metadata.get("tools"),
        field="tools",
        limit=MAX_TOOL_IDENTIFIERS,
        error_code="AGENT_TOOL_RESTRICTION_INVALID",
    )
    mcp_allow = _parse_allowlist(
        metadata.get("mcp"),
        field="mcp",
        limit=MAX_MCP_SERVERS,
        error_code="AGENT_MCP_RESTRICTION_INVALID",
    )

    try:
        body_text = body_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        _fail("agent body is not valid UTF-8")
    if not body_text or len(body_text.encode("utf-8")) > MAX_BODY_BYTES:
        _fail("agent body must be non-empty and within its limit")
    from tools.threat_patterns import scan_for_threats

    if scan_for_threats(f"{description}\n{body_text}", scope="strict"):
        _fail(
            "agent system-authority content matches a threat pattern",
            "AGENT_INSTRUCTION_THREAT_DETECTED",
        )

    relative = Path(relative_path).as_posix()
    if Path(relative).is_absolute() or relative == ".." or relative.startswith("../"):
        _fail("agent path must be profile-relative", "PROFILE_AGENT_ROOT_MISMATCH")
    if len(relative.encode("utf-8")) > MAX_RELATIVE_PATH_BYTES:
        _fail("agent relative path exceeds its limit", "AGENT_DISCOVERY_LIMIT")

    return AgentDefinition(
        name=name,
        description=description,
        identity=identity,
        instructions=body_text,
        provider=provider,
        model=model,
        fallbacks=fallbacks,
        tools_allow=tools_allow,
        mcp_allow=mcp_allow,
        relative_path=relative,
        full_digest=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _read_relative_regular_file(
    agents_root: Path,
    relative_path: Path,
    *,
    expected_root_identity: tuple[int, int] | None = None,
) -> tuple[bytes, os.stat_result, os.stat_result]:
    """Read below ``agents_root`` using no-follow descriptor-relative traversal."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    odirectory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or odirectory is None:
        _fail("secure no-follow reads are unavailable", "SECURE_AGENT_LOAD_UNAVAILABLE")
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        _fail("agent path must be profile-relative", "PROFILE_AGENT_ROOT_MISMATCH")

    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | odirectory
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow
    descriptors: list[int] = []
    try:
        root_fd = os.open(agents_root, directory_flags)
        descriptors.append(root_fd)
        root_stat = os.fstat(root_fd)
        _require_owned_restrictive(root_stat, "agents root")
        if expected_root_identity is not None and (root_stat.st_dev, root_stat.st_ino) != expected_root_identity:
            _fail("agent root identity changed", "STALE_AGENT_DEFINITION")

        parent_fd = root_fd
        for component in relative_path.parts[:-1]:
            parent_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            descriptors.append(parent_fd)
            _require_owned_restrictive(os.fstat(parent_fd), "agent path ancestor")
        descriptor = os.open(relative_path.name, file_flags, dir_fd=parent_fd)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("agent definition is not a regular file", "SECURE_AGENT_LOAD_UNAVAILABLE")
        if before.st_nlink != 1:
            _fail("hard-linked agent definitions are not allowed", "SECURE_AGENT_LOAD_UNAVAILABLE")
        _require_owned_restrictive(before, "agent definition")
        data = os.read(descriptor, MAX_AGENT_FILE_BYTES + 1)
        after = os.fstat(descriptor)
    except AgentDefinitionError:
        raise
    except OSError:
        _fail("cannot securely open agent definition", "SECURE_AGENT_LOAD_UNAVAILABLE")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if len(data) > MAX_AGENT_FILE_BYTES:
        _fail("agent definition exceeds its file-size limit")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("agent definition changed during read", "STALE_AGENT_DEFINITION")
    return data, after, root_stat


def _reject_link(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        _fail("cannot inspect agent path", "SECURE_AGENT_LOAD_UNAVAILABLE")
    if stat.S_ISLNK(mode):
        _fail("symlinks are not allowed below agents/", "SECURE_AGENT_LOAD_UNAVAILABLE")


def _validate_path_chain(agents_root: Path, path: Path) -> None:
    """Reject changed/symlinked ancestors immediately before a secure file open."""

    try:
        relative = path.relative_to(agents_root)
    except ValueError:
        _fail("agent path escaped agents root", "SECURE_AGENT_LOAD_UNAVAILABLE")
    current = agents_root
    _reject_link(current)
    for component in relative.parts[:-1]:
        current = current / component
        _reject_link(current)
        try:
            mode = current.stat().st_mode
        except OSError:
            _fail("cannot inspect agent path", "SECURE_AGENT_LOAD_UNAVAILABLE")
        if not stat.S_ISDIR(mode):
            _fail("agent path ancestor is not a directory", "SECURE_AGENT_LOAD_UNAVAILABLE")


def discover_profile_agents(profile_root: Path | str) -> AgentCatalog:
    """Build a deterministic immutable catalog under ``profile_root/agents``."""

    profile = Path(profile_root).absolute()
    agents_root = profile / "agents"
    try:
        profile_stat = profile.stat()
    except OSError:
        _fail("profile root is unavailable", "SECURE_AGENT_LOAD_UNAVAILABLE")
    _require_owned_restrictive(profile_stat, "profile root")
    if not agents_root.exists():
        return AgentCatalog(profile, agents_root, (), hashlib.sha256(b"").hexdigest())
    _reject_link(agents_root)
    if not agents_root.is_dir():
        _fail("agents root is not a directory", "SECURE_AGENT_LOAD_UNAVAILABLE")
    agents_root_stat = agents_root.stat()

    entries_visited = 0
    total_bytes = 0
    loaded: list[AgentCatalogEntry] = []
    for directory, dirnames, filenames in os.walk(agents_root, topdown=True, followlinks=False):
        current = Path(directory)
        relative_directory = current.relative_to(agents_root)
        depth = len(relative_directory.parts)
        if depth > MAX_AGENT_DEPTH:
            _fail("agent discovery exceeds maximum depth", "AGENT_DISCOVERY_LIMIT")
        dirnames.sort()
        filenames.sort()
        for dirname in list(dirnames):
            entries_visited += 1
            candidate = current / dirname
            _reject_link(candidate)
            if entries_visited > MAX_AGENT_ENTRIES:
                _fail("agent discovery entry budget exceeded", "AGENT_DISCOVERY_LIMIT")
        for filename in filenames:
            entries_visited += 1
            if entries_visited > MAX_AGENT_ENTRIES:
                _fail("agent discovery entry budget exceeded", "AGENT_DISCOVERY_LIMIT")
            path = current / filename
            _reject_link(path)
            if path.suffix.lower() != ".md":
                continue
            if len(loaded) >= MAX_AGENT_FILES:
                _fail("agent definition count exceeded", "AGENT_DISCOVERY_LIMIT")
            relative = path.relative_to(agents_root)
            if len(relative.as_posix().encode("utf-8")) > MAX_RELATIVE_PATH_BYTES:
                _fail("agent relative path exceeds its limit", "AGENT_DISCOVERY_LIMIT")
            _validate_path_chain(agents_root, path)
            raw, file_stat, opened_root_stat = _read_relative_regular_file(
                agents_root,
                relative,
                expected_root_identity=(agents_root_stat.st_dev, agents_root_stat.st_ino),
            )
            total_bytes += len(raw)
            if total_bytes > MAX_AGENT_TOTAL_BYTES:
                _fail("agent catalog byte budget exceeded", "AGENT_DISCOVERY_LIMIT")
            definition = parse_agent_definition(raw, relative)
            loaded.append(
                AgentCatalogEntry(
                    definition=definition,
                    path=path,
                    device=file_stat.st_dev,
                    inode=file_stat.st_ino,
                    size=file_stat.st_size,
                    mtime_ns=file_stat.st_mtime_ns,
                    agents_root_device=opened_root_stat.st_dev,
                    agents_root_inode=opened_root_stat.st_ino,
                )
            )

    loaded.sort(key=lambda entry: entry.definition.relative_path)
    names: set[str] = set()
    for entry in loaded:
        if entry.definition.name in names:
            _fail(
                f"duplicate canonical agent name: {entry.definition.name}",
                "AGENT_DEFINITION_COLLISION",
            )
        names.add(entry.definition.name)
    loaded.sort(key=lambda entry: entry.definition.name)
    revision_input = "\n".join(
        f"{entry.definition.relative_path}\0{entry.definition.full_digest}" for entry in loaded
    ).encode()
    return AgentCatalog(
        profile_root=profile,
        agents_root=agents_root,
        entries=tuple(loaded),
        revision=hashlib.sha256(revision_input).hexdigest(),
    )


def reload_catalog_entry(entry: AgentCatalogEntry) -> AgentDefinition:
    """Securely reload and revalidate one immutable catalog entry."""

    agents_root = entry.path.parents[len(Path(entry.definition.relative_path).parts) - 1]
    if entry.persisted:
        current_catalog = discover_profile_agents(agents_root.parent)
        current_entry = current_catalog.get(entry.definition.name)
        if (
            current_entry is None
            or current_entry.definition.relative_path != entry.definition.relative_path
            or current_entry.definition.full_digest != entry.definition.full_digest
        ):
            _fail("persisted agent definition is no longer current", "STALE_AGENT_DEFINITION")
        return current_entry.definition

    current_catalog = discover_profile_agents(agents_root.parent)
    current_entry = current_catalog.get(entry.definition.name)
    if current_entry is None or current_entry.definition.relative_path != entry.definition.relative_path:
        _fail("agent catalog membership changed", "STALE_AGENT_DEFINITION")
    raw, current, _root_stat = _read_relative_regular_file(
        agents_root,
        Path(entry.definition.relative_path),
        expected_root_identity=(entry.agents_root_device, entry.agents_root_inode),
    )
    if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
        entry.device,
        entry.inode,
        entry.size,
        entry.mtime_ns,
    ):
        _fail("agent definition file identity changed", "STALE_AGENT_DEFINITION")
    definition = parse_agent_definition(raw, entry.definition.relative_path)
    if definition.full_digest != entry.definition.full_digest or definition.name != entry.definition.name:
        _fail("agent definition content changed", "STALE_AGENT_DEFINITION")
    return definition


def snapshot_agent_catalog(catalog: AgentCatalog) -> dict[str, Any]:
    """Return the bounded canonical catalog state persisted with a session."""

    def snapshot_definition(entry: AgentCatalogEntry) -> dict[str, Any]:
        definition = entry.definition
        payload = {
            "name": definition.name,
            "description": definition.description,
            "identity": definition.identity,
            "instructions": definition.instructions,
            "provider": definition.provider,
            "model": definition.model,
            "fallbacks": (
                None
                if definition.fallbacks is None
                else [
                    {"provider": route.provider, "model": route.model}
                    for route in definition.fallbacks
                ]
            ),
            "tools_allow": (
                None if definition.tools_allow is None else list(definition.tools_allow)
            ),
            "mcp_allow": (
                None if definition.mcp_allow is None else list(definition.mcp_allow)
            ),
            "relative_path": definition.relative_path,
            "full_digest": definition.full_digest,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {**payload, "snapshot_digest": hashlib.sha256(canonical).hexdigest()}

    snapshot = {
        "version": 1,
        "revision": catalog.revision,
        "definitions": [snapshot_definition(entry) for entry in catalog.entries],
    }
    if not catalog.entries:
        snapshot["catalog_mac"] = None
        return snapshot
    snapshot["catalog_mac"] = _snapshot_mac(
        snapshot,
        _catalog_snapshot_key(catalog.profile_root, create=True),
    )
    return snapshot


def restore_agent_catalog(snapshot: Any, profile_root: Path | str) -> AgentCatalog:
    """Restore a session-pinned catalog without consulting current Markdown files."""

    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"version", "revision", "definitions", "catalog_mac"}
        or snapshot.get("version") != 1
    ):
        _fail("stored agent catalog snapshot is invalid", "STALE_AGENT_DEFINITION")
    profile = Path(profile_root).absolute()
    catalog_mac = snapshot.get("catalog_mac")
    unsigned_snapshot = {key: value for key, value in snapshot.items() if key != "catalog_mac"}
    definitions = snapshot.get("definitions")
    if definitions == [] and catalog_mac is None:
        expected_mac = None
    else:
        expected_mac = _snapshot_mac(
            unsigned_snapshot,
            _catalog_snapshot_key(profile, create=False),
        )
    if expected_mac is not None and (
        not isinstance(catalog_mac, str) or not hmac.compare_digest(catalog_mac, expected_mac)
    ):
        _fail("stored agent catalog authentication failed", "STALE_AGENT_DEFINITION")
    revision = snapshot.get("revision")
    if not isinstance(definitions, list) or len(definitions) > MAX_AGENT_FILES:
        _fail("stored agent catalog snapshot exceeds its limit", "STALE_AGENT_DEFINITION")
    if not isinstance(revision, str) or len(revision) != 64:
        _fail("stored agent catalog revision is invalid", "STALE_AGENT_DEFINITION")

    agents_root = profile / "agents"
    entries: list[AgentCatalogEntry] = []
    total_bytes = 0
    for raw in definitions:
        if not isinstance(raw, dict) or set(raw) != {
            "name",
            "description",
            "identity",
            "instructions",
            "provider",
            "model",
            "fallbacks",
            "tools_allow",
            "mcp_allow",
            "relative_path",
            "full_digest",
            "snapshot_digest",
        }:
            _fail("stored agent definition is invalid", "STALE_AGENT_DEFINITION")
        snapshot_digest = raw["snapshot_digest"]
        digest_payload = {key: value for key, value in raw.items() if key != "snapshot_digest"}
        canonical = json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if not isinstance(snapshot_digest, str) or not hmac.compare_digest(
            snapshot_digest,
            hashlib.sha256(canonical).hexdigest(),
        ):
            _fail("stored agent definition digest mismatch", "STALE_AGENT_DEFINITION")
        instructions = raw["instructions"]
        if not isinstance(instructions, str):
            _fail("stored agent instructions are invalid", "STALE_AGENT_DEFINITION")
        total_bytes += len(instructions.encode("utf-8"))
        if total_bytes > MAX_AGENT_TOTAL_BYTES:
            _fail("stored agent catalog byte budget exceeded", "STALE_AGENT_DEFINITION")
        fallback_values = raw["fallbacks"]
        if fallback_values is not None and (
            not isinstance(fallback_values, list) or len(fallback_values) > MAX_FALLBACKS
        ):
            _fail("stored agent fallbacks are invalid", "STALE_AGENT_DEFINITION")
        fallbacks = (
            None
            if fallback_values is None
            else tuple(
                AgentFallbackRoute(
                    provider=_bounded_identifier(route.get("provider") if isinstance(route, dict) else None, "fallback.provider"),
                    model=_bounded_identifier(route.get("model") if isinstance(route, dict) else None, "fallback.model"),
                )
                for route in fallback_values
            )
        )
        tools_allow = _parse_allowlist(
            None if raw["tools_allow"] is None else {"allow": raw["tools_allow"]},
            field="tools",
            limit=MAX_TOOL_IDENTIFIERS,
            error_code="STALE_AGENT_DEFINITION",
        )
        mcp_allow = _parse_allowlist(
            None if raw["mcp_allow"] is None else {"allow": raw["mcp_allow"]},
            field="mcp",
            limit=MAX_MCP_SERVERS,
            error_code="STALE_AGENT_DEFINITION",
        )
        relative = Path(str(raw["relative_path"]))
        definition = AgentDefinition(
            name=str(raw["name"]),
            description=str(raw["description"]),
            identity=str(raw["identity"]),
            instructions=instructions,
            provider=raw["provider"],
            model=raw["model"],
            fallbacks=fallbacks,
            tools_allow=tools_allow,
            mcp_allow=mcp_allow,
            relative_path=relative.as_posix(),
            full_digest=str(raw["full_digest"]),
        )
        # Reuse the normal parser's field validation on reconstructed exact bytes.
        serialized = (
            "---\n"
            + json.dumps(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "identity": definition.identity,
                    **({"provider": definition.provider} if definition.provider else {}),
                    **({"model": definition.model} if definition.model else {}),
                    **(
                        {"fallbacks": [{"provider": r.provider, "model": r.model} for r in fallbacks]}
                        if fallbacks is not None
                        else {}
                    ),
                    **({"tools": {"allow": list(tools_allow)}} if tools_allow is not None else {}),
                    **({"mcp": {"allow": list(mcp_allow)}} if mcp_allow is not None else {}),
                }
            )
            + "\n---\n"
            + instructions
        ).encode()
        validated = parse_agent_definition(serialized, relative)
        definition = AgentDefinition(
            **{
                **validated.__dict__,
                "fallbacks": fallbacks,
                "full_digest": definition.full_digest,
            }
        )
        entries.append(
            AgentCatalogEntry(
                definition=definition,
                path=agents_root / relative,
                device=0,
                inode=0,
                size=0,
                mtime_ns=0,
                agents_root_device=0,
                agents_root_inode=0,
                persisted=True,
            )
        )
    calculated = hashlib.sha256(
        "\n".join(
            f"{entry.definition.relative_path}\0{entry.definition.full_digest}" for entry in entries
        ).encode()
    ).hexdigest()
    if calculated != revision:
        _fail("stored agent catalog revision mismatch", "STALE_AGENT_DEFINITION")
    return AgentCatalog(profile, agents_root, tuple(entries), revision)


__all__ = [
    "MAX_AGENT_FILE_BYTES",
    "MAX_AGENT_FILES",
    "MAX_AGENT_ENTRIES",
    "MAX_AGENT_TOTAL_BYTES",
    "MAX_AGENT_DEPTH",
    "MAX_RELATIVE_PATH_BYTES",
    "MAX_FRONTMATTER_BYTES",
    "MAX_FRONTMATTER_LINES",
    "MAX_DESCRIPTION_CHARS",
    "MAX_BODY_BYTES",
    "MAX_ROUTE_IDENTIFIER_CHARS",
    "MAX_FALLBACKS",
    "MAX_TOOL_IDENTIFIERS",
    "MAX_MCP_SERVERS",
    "AgentDefinitionError",
    "AgentFallbackRoute",
    "AgentDefinition",
    "AgentCatalogEntry",
    "AgentCatalog",
    "parse_agent_definition",
    "discover_profile_agents",
    "reload_catalog_entry",
    "snapshot_agent_catalog",
    "restore_agent_catalog",
]
