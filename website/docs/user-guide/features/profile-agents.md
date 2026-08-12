---
sidebar_position: 8
title: "Profile agents"
description: "Define reusable, profile-scoped subagents in Markdown"
---

# Profile agents

Profile agents are reusable subagent definitions owned by one Hermes profile. Put each definition in a Markdown file under:

```text
$HERMES_HOME/agents/
```

Hermes discovers `agents/**/*.md` when it creates a conversation and pins the resulting catalog to that conversation. Editing a file does not silently change an existing pinned definition. A later launch revalidates the current file and fails closed if its path, name, digest, catalog revision, or security properties changed.

Profile agents reuse the existing `delegate_task` lifecycle: provider resolution, fallbacks, depth and concurrency limits, cancellation, transcripts, accounting, finalization, and role restrictions remain host-owned.

## File format

Every file contains strict YAML frontmatter followed by a non-empty Markdown body:

```md
---
name: researcher
description: Research current facts and cite authoritative sources
identity: replace
provider: openrouter
model: openai/gpt-5-mini
fallbacks:
  - provider: openrouter
    model: openai/gpt-4.1-mini
tools:
  allow:
    - web_search
    - web_extract
mcp:
  allow:
    - context7
skills:
  - grounded-citations
---

Verify claims against current authoritative sources. Return concise findings with links.
```

Required fields:

- `name`: canonical lowercase identifier using letters, digits, and hyphens.
- `description`: bounded plain text used for catalog discovery.
- `identity`: exactly `profile` or `replace`.
- Markdown body: the agent's reusable instructions.

Optional routing fields:

- `provider` and `model`: configured route identifiers, never credentials or endpoints. An explicit provider requires an explicit model.
- `fallbacks`: ordered `{provider, model}` routes. Omission inherits the delegation fallback chain; `[]` disables per-agent fallbacks; a non-empty list replaces the inherited chain.

Optional startup guidance:

- `skills`: ordered canonical skill names to load when the delegated child is created. Hermes pins each skill's exact `SKILL.md` bytes in the signed conversation catalog, rejects missing, disabled, ambiguous, linked, changed, or oversized skills, and injects the authenticated contents through the same stable prompt tier as the agent definition.
- Startup loading is deliberately passive: it does not run inline shell, setup, secret capture, template substitution, or usage hooks. Skill declarations never add tools or toolsets.

Unknown fields, duplicate routes, unsafe YAML features, malformed files, unsafe path permissions, links, and scanner-positive system-authority content are rejected.

## Identity modes

- `identity: profile` reloads the selected profile's `SOUL.md` (or Hermes' default identity) and installs the Markdown body as stable additive child guidance.
- `identity: replace` installs the Markdown body in the child's stable identity slot instead of `SOUL.md` or the default identity.

Hermes' normal tool rules, task/context guidance, workspace, platform, lifecycle, reporting, and safety guidance still wrap either identity mode.

## Tool and MCP restrictions

`tools.allow` and `mcp.allow` only narrow authority. They cannot grant anything the parent agent does not already have.

`skills` is guidance, not authority. Declaring a skill does not change the intersection below.

```text
child authority = parent exact authority ∩ requested allowlist ∩ role policy
```

Semantics are category-specific:

- field omitted: inherit the parent's authority for that category;
- `allow: []`: grant none from that category;
- non-empty `allow`: exact-name intersection with the parent's authority;
- unknown/unavailable tool or MCP server: launch fails instead of silently widening or ignoring it.

`tools.allow` uses exact runtime tool names such as `web_search` and `read_file`. `mcp.allow` uses configured MCP server names, not generated MCP tool-name prefixes.

The same exact authority is enforced for direct dispatch, plugin tools, deferred `tool_search`/`tool_call`, `execute_code`, MCP refresh, and nested delegation. Leaf/orchestrator role restrictions are applied in addition to the file's allowlists.

## Catalog and launch surfaces

- CLI: `/agents definitions` lists the pinned catalog and `/agents show <name>` shows its safe projection. `/agents` without arguments still shows active runs.
- TUI: the Agents overlay contains Runs and Definitions views and launches through a session-bound typed RPC.
- Desktop: Agents contains Runs and Definitions views and uses the same typed RPC.
- Model delegation: `delegate_task` may select a catalog entry by canonical `agent_name`; provider, model, fallbacks, body, and exact authority remain host-resolved.

The projection intentionally excludes the private Markdown body, absolute paths, credentials, endpoints, and secret scope.

Messaging gateways support the same read-only `/agents definitions` and `/agents show <name>` projections while `/agents` without arguments remains the existing run monitor. Gateways do not add a text-based definition launch command; typed TUI/Desktop launch and `delegate_task` remain the execution paths.

ACP and the web dashboard do not currently provide dedicated profile-agent catalog surfaces. ACP still receives the model-facing `delegate_task` schema and can delegate through it; the web dashboard has no Agents definitions view. Use CLI, a messaging gateway, TUI, Desktop, or the delegation tool instead.

New catalog labels in Desktop and TUI are currently English-only, matching those Agents views' existing string strategy. Gateway run-monitor labels remain localized; the new definition subcommands intentionally return compact protocol-neutral metadata.

## Profile lifecycle

- `profile create --clone-all` copies definitions but removes the source catalog-signing key so the destination establishes its own catalog identity.
- Profile export/import preserves `agents/` but excludes or removes `.agent-catalog-signing-key`.
- Session resume restores the signed pinned catalog for historical identity, but every new launch revalidates the current profile files before execution.

## Errors and reload behavior

Common fail-closed errors include:

- `STALE_AGENT_DEFINITION`: catalog revision, file identity, path, name, or digest changed;
- `AGENT_TOOL_UNAVAILABLE`: a requested tool is absent from parent authority;
- `AGENT_MCP_UNAVAILABLE`: a requested MCP server is absent from parent authority;
- `AGENT_SKILL_UNAVAILABLE`: a declared startup skill is missing, disabled, ambiguous, unsafe, or cannot be loaded under the profile skill root;
- `AGENT_ROUTE_UNAVAILABLE`: the configured route cannot resolve;
- `AGENT_LAUNCH_REPLAY`: a typed launch request was already consumed.

Start a new conversation to intentionally capture a changed catalog. Do not copy catalog-signing keys between profiles.
