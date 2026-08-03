# Pre-PR surface audit: profile agents and exact authority

Status: current local implementation audit; no commit, push, issue, or PR has been created.

## Surface matrix

| Surface | Status | Evidence / contract |
|---|---|---|
| Definition parser and bounded discovery | Covered | `agent/agent_definitions.py`; strict schema, bytes/line/count limits, canonical collisions, ownership/mode/link/hardlink checks, threat scan. |
| Conversation catalog and session restore | Covered | Catalog pinned on agent initialization; signed snapshot restoration revalidates current profile files before launch. |
| Model schema | Covered | `delegate_task.agent_name` is a catalog enum; body, absolute path, route, digest, credentials and authority metadata stay host-side. |
| Direct delegation and batches | Covered | Exact parent intersection, role restrictions, route/authority preflight before any child construction, nested propagation. |
| Direct tool dispatch | Covered | Final exact-name check in model dispatch/executor. |
| Composite toolsets and plugins | Covered by shared registry boundary | The resolved parent snapshot is intersected after registry/toolset expansion; plugins cannot grant a name absent from parent authority. |
| Deferred tools | Covered | `tool_search`, `tool_describe`, and `tool_call` expose and dispatch only exact granted names. |
| `execute_code` | Covered | Local and remote transports distinguish unrestricted `None` from explicit empty/reduced authority; no fallback to all tools. |
| MCP | Covered | Server provenance is explicit; `mcp.allow`, late refresh, and nested delegation preserve exact parent authority. |
| CLI | Covered | `/agents`, `/agents definitions`, `/agents show <name>`; safe projection now includes tool/MCP policy. |
| Messaging gateways | Covered read-only | `/agents` keeps run monitoring; `definitions` and `show` read the conversation-pinned catalog. No text launch path was added. |
| TUI | Covered | Runs/Definitions overlay, session-bound versioned RPC launch, projection v2. |
| Desktop | Covered | Existing Agents page contains Runs/Definitions; session-bound versioned RPC launch, projection v2 and policy display. |
| RPC | Covered | `agent_definitions.list` and `.launch` bind session/profile/revision/digest/request ID; launch replay is durable and single-use. |
| Profile clone | Covered | Definitions copy under `clone_all`; catalog signing key does not. |
| Profile export/import | Covered | `agents/` is portable; source catalog signing key is excluded/removed. |
| ACP | Shared runtime; dedicated proof pending after rebase | ACP uses the same agent runtime and `delegate_task` registry; no separate catalog UI is required. Add a small ACP-platform schema/intersection regression on fresh main before PR-ready status. |
| Web dashboard | Not applicable for v1 UI | No existing Agents definitions surface; adding one would create a second UI/API consumer without an accepted need. Runtime delegation remains shared. |
| i18n | Explicit limitation | Existing Desktop/TUI Agents definitions labels are English-only. Gateway's existing run monitor remains localized; definition projections are compact metadata. |
| Documentation | Covered | `website/docs/user-guide/features/profile-agents.md`, delegation/profile cross-links, sidebar entry, this audit. |

## Verification performed on this state

- Focused Python matrix after closing the final review blockers: 812 passed.
- Desktop typecheck: passed; targeted Agents tests: 4 passed.
- TUI typecheck: passed; targeted Agents tests: 2 passed.
- Python compilation and `git diff --check`: passed.
- Docusaurus production build: passed after an isolated `npm ci` with the repository's declared website dependencies. The ambient npm version was below the website's declared minimum, so `engine-strict` was disabled for this local verification only; no package manifest or lockfile changed.

## Remaining gates before a PR

1. The final code review returned REQUEST CHANGES for nested exact-authority propagation and durable request-ID rebinding. Both reproductions are fixed and covered by regression tests. The paired security review was provider-filtered and produced no verdict; do not launch another reviewer without explicit user approval.
2. Rebuild the implementation branch from a fresh, current `main` according to the user's PR workflow, then rerun the complete matrix. The current branch is 87 upstream commits behind and also contains an unrelated voice commit, so it is not a valid PR head.
3. Recheck open/closed/merged prior art immediately before proposing slices. Current high-overlap opens include #25036 and #25066; the acceptance matrix must explain why this implementation is narrower and current-main compatible.
4. Decide PR slicing. The safest upstream proposal may separate: (a) core catalog/delegation, (b) exact tool/MCP authority, and (c) UI/docs. Do not publish any slice without explicit approval.
5. Add an explicit ACP-platform regression after rebase and retain the existing shared-registry proof for plugin tools. Do not claim ACP as independently tested until that passes.

## Non-applicable or intentionally deferred surfaces

- No new runner, lifecycle, registry, permission system, provider, credential store, MCP manager, plugin API, web dashboard API, ACP command, watcher, or persistent agent process.
- No `tools.deny`; the schema is closed and narrowing-only.
- No direct model control over provider/model/fallback/toolset/MCP arguments outside selecting an operator-authored catalog capability.
