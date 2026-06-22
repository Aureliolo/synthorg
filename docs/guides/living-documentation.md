---
title: Living Documentation
description: Browse and search the per-project document wiki that agents write to and retrieve from automatically.
---

# Living Documentation

Living documentation is a per-project document system that serves two purposes at once: documents are browsable as a wiki in the dashboard, and they are chunked, embedded, and stored in a project-doc retrieval namespace so they are transparently retrieved by any agent running on that project. Agents write status reports, deliverables, and knowledge notes through an in-process tool; writes are committed on a dedicated `synthorg/docs` git branch in the project workspace, so git history is the canonical version record. The design goal is that the org documents itself: a status report written by one agent on one task is immediately discoverable by a different agent on a later task, with no special-casing in agent code.

The implementation lives under `src/synthorg/docs_engine/`. See the [living documentation design spec](../design/living-documentation.md) for the full architecture.

## Concepts

- **Living document**: the core model, with a slug (kebab-case, derived from the title), a document type, and a body of typed blocks.
- **Document types**: `status_report`, `deliverable`, `knowledge_note`, `codebase_analysis`, `run_narrative`.
- **Document blocks**: a discriminated union (heading, prose, bullet list, code, decision, metric, link); each block carries a stable id.
- **Retrieval facade**: when any agent on a project retrieves memories, the facade fans out to the agent's own memories and to the project-doc entries scoped to that project, merging by relevance.
- **Boot-time replay**: on startup, any gap between the workspace head commit and the last indexed commit triggers an idempotent re-index.

## Enablement

There is no boolean toggle. The engine self-enables at startup when all three infrastructure dependencies are present: a connected persistence backend, a project workspace service, and a memory backend. If any is absent, the controller stubs return `503`.

Chunk budgets, search limits, and the git branch name are module constants in `src/synthorg/docs_engine/constants.py` (the branch is `synthorg/docs`, the retrieval namespace is `project_docs`); they are part of the on-disk index contract and are not runtime-tunable.

## Endpoints

All endpoints are read-only and require read access. Writes happen in-process via the agent tool or the admin-gated MCP handler, not over REST. Base path: `/projects/{project_id}/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects/{project_id}/docs` | Paginated document summaries, recency-first. Supports `?doc_type=` and `?tag=` filters. |
| `GET` | `/projects/{project_id}/docs/search` | Semantic search across indexed docs. Required `?q=`, optional `?limit=` (1 to 64, default 8). |
| `GET` | `/projects/{project_id}/docs/{slug}` | Fetch one document by slug (404 if absent). |
| `GET` | `/projects/{project_id}/docs/{slug}/history` | Git revision history for a document, optional `?limit=` (default 50). |

## Worked example: browse and search

List the most recent documents for a project:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/<project_id>/docs?limit=20"
```

Semantic search across a project's docs:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/<project_id>/docs/search?q=checkout+performance&limit=5"
```

Retrieve a document's git history:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/<project_id>/docs/q3-status-report/history?limit=10"
```

## Observability

The docs engine emits structured log events only (no WebSocket events): `docs.doc.written`, `docs.doc.indexed`, `docs.doc.commit_pushed`, `docs.search.start` / `.complete`, and `docs.facade.fanout`, plus failure variants. The dashboard surfaces the wiki at `/projects/:projectId/docs`.
