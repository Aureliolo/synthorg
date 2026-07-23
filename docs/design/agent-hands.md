# Agent Hands: First-Party Forge Tools

Agent hands are the first-party, connection-gated tools an agent uses to
land its own work on a code forge: read a repo, open and comment on issues
and pull requests, review with inline diff-anchored comments, drive CI, and
push a branch or file so a pull request can be opened. They are built on the
native connection catalog and the forge agent-client registry, not a
third-party MCP server, so credential brokering, the approval gate, and the
egress host-pin are the ones already in place.

## Tool surface

All tools live under `synthorg/tools/forge/`, subclass the shared
`_BaseForgeTool` (itself a `GovernedConnectionTool`), and dispatch on an
`action` field. Egress is pinned to the bound connection's host by
construction, so an agent can never redirect a call to another host.

| Tool | Actions | Writes |
| --- | --- | --- |
| `forge_repo` | `get_repo`, `read_file`, `list_dir` | none (read-only) |
| `forge_issue` | `get`, `list`, `open`, `comment` | `open`, `comment` |
| `forge_pull_request` | `get`, `list`, `open`, `comment`, `review`, `merge` | all but `get`/`list` |
| `forge_push` | `create_branch`, `write_file` | all |
| `forge_ci` | `list_runs`, `get_run`, `trigger`, `rerun` | `trigger`, `rerun` |

Agent-authored titles, bodies, and commit messages pass through the
output-style policy (`_guard_forge_text`) before any write reaches the
forge, so a hard-rule violation (the em-dash ban) is rejected or rewritten
at the boundary.

### Branch and file on-ramp (`forge_push`)

`forge_push` is the on-ramp for an agent landing its own work through the
forge API: `create_branch` opens a feature branch, `write_file` commits
file contents to it, and `forge_pull_request open` then raises the PR.
`forge_push` binds its own `ActionType.VCS_PUSH` (never the shared
`comms:external`), so the existing `GitAccess` sub-constraint ladder and
the `DEFAULT_RISK_MAP` HIGH tier govern it, and an autonomy grant written
for chat can never auto-approve a push. A force-push pattern in the commit
message is auto-escalated by the security rules engine's
`DestructiveOpDetector`.

### Inline review comments and CI

A `forge_pull_request review` carries optional `comments`, each anchored to
a `path` / `line` / `side` on the diff; the forge attaches them to the
review. `forge_ci trigger` / `rerun` mutate CI state and route through the
approval gate; a forge without a CI API (Gitea/Forgejo) fails loud with
`ForgeUnsupportedError` rather than faking a result.

## Forge clients

The `forge_agent_api_client` registry (`engine/workspace/git_backend/
forge_api/`) is keyed by `ConnectionType`:

- **GitHub** and the **Gitea family** (Gitea + Forgejo) share the
  GitHub-compatible read surface on `ForgeAgentBase`; each supplies its own
  write divergences (review event vocabulary, one-shot vs resolve-then-ref
  branch creation, label-by-id resolution).
- **GitLab** implements the `ForgeAgentApiClient` protocol directly on the
  GitLab transport: its v4 API addresses repositories as a URL-encoded
  `namespace/project` path, keys issues and merge requests by a per-project
  `iid`, models a review as an approval plus diff-anchored discussions, and
  runs CI as pipelines. Its files API returns no commit sha, so the client
  resolves the branch head after a write.

A forge without a wired agent client surfaces a typed
`ForgeUnsupportedError` at the tool boundary.

## Repository scoping (fail-closed, least-privilege)

An org-wide token can reach many repositories; agent hands must act only on
the ones an operator selected. Scope is expressed as the connection's
`allowed_repos` (`owner/repo` entries, `owner/*` globs permitted) and
enforced once in `_BaseForgeTool._resolve_connection`, which rejects any
`owner/repo` outside the scope with `ForgeRepoScopeError` **before** the
approval gate (an out-of-scope call is refused outright, never parked).

The scope is **fail-closed**: an empty `allowed_repos` denies every
repository. An operator populates it through a discovery flow rather than
guessing globs:

1. `GET /connections/{name}/accessible-repos` scans the repositories the
   connection's token can reach (`list_accessible_repos` on the client).
2. The operator selects the in-scope repositories in the dashboard
   connection editor.
3. The selection persists on the `Connection` record (`allowed_repos`),
   round-tripped on SQLite and Postgres.

The `check_forge_repo_scoped.py` convention gate guards the enforcement:
`_BaseForgeTool._resolve_connection` must raise `ForgeRepoScopeError`, and
no forge tool may override `_resolve_connection` without re-enforcing scope
(opt out per-class with `# lint-allow: forge-repo-scoped -- <reason>`).

## Governance summary

- Reads on a `sensitive` connection and every write route through the
  identity-bound `ConnectionApprovalGate`.
- `forge_push` carries `vcs:push`; other forge writes carry
  `comms:external`; each is risk-classified independently.
- Credentials are brokered per call from the connection catalog, travel in
  the `Authorization` header only, and are never logged.
- Repo scope is checked before the gate; out-of-scope calls are refused.
