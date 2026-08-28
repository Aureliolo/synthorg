---
title: Brownfield Intake
description: Import an existing codebase into a project, build a structure map, and run an automated analysis pass.
---

# Brownfield Intake

Brownfield intake is the entry mode for pointing the org at an existing codebase. The operator supplies a source reference (a local path or a remote clone URL); the platform imports it into a persistent project workspace, builds a deterministic navigable structure map, runs an agent analysis pass that authors a codebase-analysis [living document](living-documentation.md), and indexes both the codebase and the assessment into the [knowledge substrate](knowledge-substrate.md). It is the codebase counterpart to requirement intake: where requirement intake turns a stated need into work, brownfield intake turns an existing repository into a mapped, indexed, analysed base the org can extend.

The implementation lives under `src/synthorg/engine/brownfield/` and `src/synthorg/api/controllers/brownfield.py`. See the [brownfield intake design spec](../design/brownfield-intake.md) for the full architecture.

## Concepts

- **Import submission**: the request (`project_id`, `source_ref`, `title`, `requested_by`, `default_branch`).
- **Source resolver**: classifies the source as a local path or a remote URL, enforces SSRF protection, and injects forge tokens from the connection catalogue. URLs with embedded credentials are rejected.
- **Structure map**: a frozen, navigable model persisted one-to-one per project (modules, entry points, test suites, build files, dependencies), built by deterministic per-ecosystem scanners (Python, Node, Go, Rust ship, with a generic file-tree fallback). A content hash over structural facts powers idempotent re-import.
- **Codebase analysis**: the living document the agent analysis pass authors, auto-indexed so agents retrieve their own understanding on later work.
- **`query_structure_map`**: the agent-facing tool that lists a requested facet with an optional name filter.

## Enablement

The controller is mounted unconditionally. The adapter wires at startup and is failure-tolerant: it requires a work pipeline, a connected persistence backend, a project workspace service, and a knowledge service. If any is missing, the controller returns `503` on every request.

The relevant tunables are the per-operation rate-limit and concurrency settings in the `api` namespace (`src/synthorg/settings/definitions/api.py`):

| Key | Type | Default | Purpose |
|---|---|---|---|
| `api.per_op_rate_limit_enabled` | bool | `true` | Master switch for per-operation sliding-window rate limits. |
| `api.per_op_rate_limit_overrides` | JSON | `{}` | Override the `brownfield.import` rate, e.g. `{"brownfield.import": [20, 60]}`. |
| `api.per_op_concurrency_enabled` | bool | `true` | Master switch for per-operation inflight concurrency caps. |
| `api.per_op_concurrency_overrides` | JSON | `{}` | Override the `brownfield.import` inflight cap, e.g. `{"brownfield.import": 2}`. |

The default policy is 10 imports per 60 seconds per user, with a single concurrent import per user.

## Endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `POST` | `/api/v1/brownfield/import` | Submit an import and analysis run for an existing codebase. | `202 Accepted`, the standard `ApiResponse` envelope with `data: {project_id, status: "accepted"}` |

The `202` acknowledges the submission and nothing more. The whole pipeline (clone or copy, scan, index, then the analysis work item) runs in a background task the endpoint spawns, so every outcome after acceptance, including every rejection, surfaces through the project's structure map, the task board, and the `brownfield.*` log events rather than through the HTTP response.

## Worked example: import a repository

```bash
curl -s -X POST https://<host>/api/v1/brownfield/import \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "acme-payments-platform",
    "source_ref": "https://example.com/acme/payments-platform",
    "title": "ACME Payments Platform",
    "requested_by": "alice",
    "default_branch": "main"
  }'
```

| Field | Required | Default | Description |
|---|---|---|---|
| `project_id` | yes | (required) | Target project to import into (must exist). |
| `source_ref` | yes | (required) | Remote clone URL (`https://` or `ssh://`) or local `file://` path. |
| `title` | no | `Imported codebase` | Title for the indexed knowledge source. |
| `requested_by` | no | `operator` | Identifier of the requester. |
| `default_branch` | no | `main` | Branch to provision and seed. |

## Re-import policy

- No existing structure map: a fresh import runs.
- Same source reference with an unchanged content hash: the re-scan short-circuits (idempotent). A changed hash re-scans and re-indexes.
- A different source reference onto an occupied project: refused. Importing over an existing codebase is destructive, so the service raises `BrownfieldWorkspaceNotEmptyError` (a `409`-shaped error) and logs `brownfield.import.rejected` with `reason=different_source`.

That refusal reaches the operator through the log and the unchanged structure map, not through the HTTP response, because the endpoint has already returned `202` by the time the pipeline reads the workspace. There is no reset endpoint: to point a project at a different codebase, use a different project.

## Observability

Progress is observable through the `brownfield.*` structured log events, including `brownfield.import.started`, `brownfield.workspace.seeded`, `brownfield.structure.scanned`, `brownfield.codebase.indexed`, and `brownfield.import.completed`, plus warning-level keys for parse failures and pipeline errors after the initial acknowledgement.
