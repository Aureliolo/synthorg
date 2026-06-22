---
title: Project Brain
description: Query a project's structured decision log covering decisions, open questions, blockers, risks, dependencies, and plan revisions.
---

# Project Brain

The project brain is a per-project, structured, queryable state store that records the six kinds of first-class project facts (decisions, open questions, blockers, risks, dependencies, and plan revisions) along with their rationale, status, confidence, and provenance citations. It is append-only with a full revision history, a git-versioned JSON snapshot on the `synthorg/docs` branch, and a retrieval-indexed memory category, so agents resuming after a multi-session gap can retrieve what is decided, what is open, what is blocked, and why, without re-deriving it from scratch. The brain is distinct from agent memory and from the approval-gate decision record.

The implementation lives under `src/synthorg/project_brain/` and `src/synthorg/api/controllers/project_brain.py`. See the [project brain design spec](../design/project-brain.md) for the full architecture.

## Concepts

- **Brain entry**: the envelope model, with a stable `entry_id`, a server-assigned `revision`, an entry kind, a title, a rationale, a status, an author, a recorded-at timestamp, optional related task and entry ids, tags, a confidence score, and citations.
- **Entry kinds**: `decision`, `open_question`, `blocker`, `risk`, `dependency`, `plan_revision`. Each kind has its own legal statuses (for example a decision is `accepted` or `superseded`; a blocker is `blocked` or `cleared`).
- **Append-only**: every status change or field update is a new revision; records are never mutated in place.
- **Three-layer storage**: a SQL table (system of record), a git JSON snapshot (portable, human-readable), and a memory index (retrieval re-entry).
- **Transparent re-entry**: the retrieval facade fans out to the brain alongside agent memories and project docs on every agent retrieval, so no special-casing is needed in agent code.
- **Steering integration**: a [mid-flight steering](mid-flight-steering.md) redirect writes a plan-revision entry tagged `steering` before the directive propagates.

## Enablement

There is no boolean toggle and no dedicated settings namespace. The subsystem wires at boot when all three of the following are present: a persistence backend, a project workspace service, and a memory backend. If the memory backend is absent, the endpoints and MCP tools return `503`.

The relevant memory settings (`src/synthorg/settings/definitions/memory.py`) are `memory.backend` (default `mem0`, restart required) and `memory.default_level` (default `persistent`). Search and pagination limits are module constants in `src/synthorg/project_brain/constants.py`.

## Endpoints

All endpoints are read-only and require read access. Writes happen in-process via agent tools or MCP. Base path: `/projects/{project_id}/brain`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects/{project_id}/brain` | Current-state projection, newest-first. Query params: `cursor`, `limit` (default 50), `entry_kind`, `status`. |
| `GET` | `/projects/{project_id}/brain/search` | Semantic search. Required `q`, optional `limit` (default 8, max 64). |
| `GET` | `/projects/{project_id}/brain/{entry_id}` | Latest revision of one entry (404 if absent). |
| `GET` | `/projects/{project_id}/brain/{entry_id}/history` | Git-snapshot history, newest-first, optional `limit` (default 50). |

## Worked example: query project state

List the current blockers for a project:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/proj-abc123/brain?entry_kind=blocker&status=blocked&limit=20"
```

Semantic search across all brain entries:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/proj-abc123/brain/search?q=payment+integration+risk&limit=5"
```

Fetch a single entry's latest revision:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/proj-abc123/brain/entry-xyz789"
```

## Observability

The brain emits structured log events only (no WebSocket events): `project_brain.entry.appended`, `project_brain.entry.revised`, `project_brain.snapshot.written`, `project_brain.entry.indexed`, `project_brain.search.start` / `.complete`, and `project_brain.replay.*`, plus failure variants.
