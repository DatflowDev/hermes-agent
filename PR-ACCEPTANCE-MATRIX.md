# Upstream PR acceptance matrix: profile Markdown agents

Status: pre-publication gate. No commit, push, issue, comment, or PR has been created.

Evidence refreshed: 2026-08-03 against `NousResearch/hermes-agent`.

## Historical disposition matrix

| PR | Surface | Disposition and evidence | Consequence for this work |
|---|---|---|---|
| #25036 — native named agent registry | New registry/tool, project-local Markdown, model-facing assignment, unenforced metadata | Open; maintainer automation marked `keep_open`, salvageability low. It specifically rejected stale `delegate_task(toolsets=...)`, unscanned project-local prompt bodies, and declared-but-unenforced controls. | Reuse no runner or project-local discovery. Keep profile-only bounded parsing, scan system-authority text, reject unknown fields, and enforce every declared authority field. Coordinate rather than presenting this as unrelated prior art. |
| #25066 — complete named-agent stack | Registry + runner + trace + routing + tools | Open draft; `keep_open`, salvageability low. Review rejected a second runner/trace path, stale tool dispatch, raw task preview persistence, and per-agent provider/model routing. | Reuse the canonical `delegate_task` lifecycle and existing traces only. Do not persist task previews. The routed portion is not acceptable upstream unchanged. |
| #28940 — per-call provider routing | Model-facing provider/model arguments | Closed after maintainer statement: “We do not want this.” | No model-facing provider/model fields. Hidden indirection is not automatically acceptable if a model-selected identifier still chooses a route. |
| #71728 — named delegation profiles | Model-selected profile chooses credentials/route | Closed `not_planned`, high confidence, under standing delegation-model-routing policy. | A model-selected `agent_name` whose definition contains provider/model has the same material effect and must not be proposed without an explicit maintainer policy change. |
| #72834 — per-call provider/model override | Per-call route override | Closed, unmerged. | Confirms the standing rejection. |
| #73405 — operator-controlled capability lanes | Fixed labels resolve to configured routes | Closed `not_planned` even though raw route values stayed out of model schema. | This is the closest precedent: hiding provider/model behind a label does not avoid the policy. |
| #73917 — sealed delegation profiles | Named profile pins route/reasoning/fallback | Closed `not_planned` despite tests and repeated clean rebases. | Strong evidence that implementation quality alone will not make named route selection acceptable. |
| #75962 — trusted task-routing profiles | Named task profiles select provider/model/reasoning | Closed `not_planned`. | Reconfirms that a trusted configured profile selected per task is still rejected. |
| #41358 — visual workflow builder | Dashboard workflow + model-controlled routing/toolsets | Open; `keep_open`, salvageability low; author later removed model-routing/toolset arguments. | Do not add a web-dashboard workflow surface or model-controlled toolsets. Web UI is intentionally out of the minimal PR. |
| #76480 — CLI subagent model picker | Operator CLI edits global delegation model/provider | Open; salvageability medium. Review asks only that provider-only state match runtime. | Operator-controlled global routing remains the accepted direction. It does not authorize model-selected per-agent routes. |
| #72501 — public subagent lifecycle API | Existing child lifecycle/plugin hooks | Merged. | Reuse current child construction, lifecycle, cancellation, accounting and finalization; do not add a second runner or trace system. |

## Acceptance versus rejection pattern

Accepted or salvageable work tends to:

- extend an existing canonical seam rather than create a second runner;
- keep provider/model selection operator-controlled and global;
- enforce declared behavior end to end;
- include focused tests and current-main compatibility;
- avoid persisting raw task or prompt previews;
- keep model-facing tool authority narrow and non-configurable.

Rejected or blocked work repeatedly:

- lets a model-selected per-call name, profile, lane, or task choose provider/model/credentials;
- reintroduces model-facing toolsets;
- declares security/limit fields that runtime does not enforce;
- creates parallel registries, runners, traces, or dashboard execution systems;
- targets stale delegation APIs or carries unresolved current-main conflicts.

## Current implementation classification

### Locally valid and potentially upstream-compatible

- profile-only, bounded Markdown discovery;
- closed schema and safe path/ownership/link handling;
- stable `identity: profile|replace` composition through Hermes' existing prompt/lifecycle seams;
- safe read-only catalog projection;
- strict `tools.allow` and `mcp.allow` narrowing as parent exact authority intersection;
- enforcement across direct dispatch, composite toolsets, deferred tools, `execute_code`, plugins, MCP refresh and nested delegation;
- session-pinned catalogs, signed restore, launch-time revalidation and replay protection;
- reuse of the existing `delegate_task` lifecycle;
- CLI/TUI/Desktop/RPC/docs surfaces without a second runner.

### Upstream policy blocker

The current definition format permits `provider`, `model`, and `fallbacks`, while the parent model selects `agent_name`. Therefore the model-selected name indirectly chooses a per-task route. PRs #71728, #73405, #73917 and #75962 show that hiding route values behind a trusted configured name or capability label still falls under the standing `delegation-model-routing` rejection policy.

This is a product-policy blocker, not a test failure. The current local feature may remain useful, but it must not be represented as upstream-ready unchanged.

## Recommended PR boundary

The smallest evidence-backed upstream route is:

1. Rebase from a fresh `upstream/main` branch and exclude the unrelated voice commit.
2. Preserve the local full implementation separately.
3. For an upstream candidate, remove per-definition provider/model/fallback selection from the model-selectable definition contract, or require an explicit maintainer policy decision before retaining it.
4. Keep profile-scoped identity/instructions plus exact tool/MCP narrowing, existing lifecycle reuse, safe projections, and focused docs/tests.
5. Avoid a web-dashboard API/UI until the human-facing definition contract is accepted.
6. Coordinate with the primary prior-art owner of #25036/#25066 and the maintainer owning current delegation policy; do not present overlapping registry work without attribution.

## Mandatory pre-open gates

- Fresh branch from current `upstream/main`; current workspace is 87 commits behind and contains an unrelated voice commit.
- Resolve the already-running final code/security verdicts without launching replacements.
- Choose and document the route-policy boundary above.
- Re-run full Python, frontend, docs, format and current-main integration checks after the rebase.
- Produce a narrowly scoped PR description with explicit prior-art attribution and no unsupported compatibility claims.
- Obtain explicit user approval before commit, push, issue, comment, or PR creation.
