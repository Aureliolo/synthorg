---
title: Knowledge Substrate
description: Ingest external corpora (PDFs, web pages, repositories) into a provenance-tracked RAG layer agents can cite.
---

# Knowledge Substrate

The knowledge substrate is the heavy-duty document and knowledge retrieval layer for ingested external corpora (PDFs, web pages, source repositories, tickets). It is distinct from both agent memory (what an agent remembers about a run) and [living documentation](living-documentation.md) (the organisation's internal wiki). Its defining feature is a provenance model precise enough to resolve every retrieved chunk back to an exact source region: page and bounding box for PDFs, line span and AST path for code, URL and character offset for web pages, so agent answers carry citable grounding rather than paraphrased recall.

The implementation lives under `src/synthorg/knowledge/`. See the [knowledge substrate design spec](../design/knowledge-substrate.md) for the full architecture.

## Concepts

- **Knowledge source**: an ingested corpus item (PDF, web page, repository, ticket) with a content hash for freshness tracking.
- **Knowledge chunk**: a structure-aware unit of a source (AST-aware for code via tree-sitter, offset-based for documents), each with its own content hash and a provenance locator.
- **Provenance locator**: a discriminated union over source type (PDF page and bounding box; web URL and offset; code path, line span, and symbol; ticket id and comment).
- **Citation**: returned with every hit, resolving a chunk back to its source id, title, uri, and locator.
- **Retrieval modes**: transparent (the retrieval facade fans out across agent memories, project docs, the knowledge namespace, and the project brain at once) and explicit (a corpus-only search tool or MCP handler).
- **Freshness**: a source's content hash short-circuits re-ingest when unchanged; on change, only changed or new chunks are re-embedded, and removed chunks are deleted.

## Enablement

The substrate is on by default. It is ghost-wired at boot whenever both a connected persistence backend and a memory backend exist, and the master switch is read live per request at the handlers, so toggling it takes effect on the next call with no restart. Turning it off, or booting without either dependency, makes the controllers and MCP handlers answer `503`.

Runtime settings (`src/synthorg/settings/definitions/knowledge.py`), all live:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `knowledge.enabled` | bool | `true` | Master switch for ingestion and retrieval. |
| `knowledge.synthesis_enabled` | bool | `true` | Whether the generative `ask` surface is available. Retrieval is unaffected by it. |
| `knowledge.synthesis_model` | MODEL_REF | `""` | The `(provider, model)` pair the synthesiser dispatches on. Unset leaves `ask` answering `503`; `search` stays available. |
| `knowledge.synthesis_synthesizer` | str | `llm` | Synthesis strategy discriminator. |
| `knowledge.synthesis_max_chunks` | int | (constant) | Retrieved chunks the synthesiser may ground on. |

`KnowledgeConfig` (`src/synthorg/knowledge/config.py`) carries the boot-time strategy discriminators `pdf_loader` (default `pdfplumber`) and `code_chunker` (default `tree_sitter`), plus `repo_root`, the operator-configured filesystem root that bounds repository and PDF ingestion. Its own `enabled` field is **not** consulted for the enable decision; the settings-service flag above is the single owner of that.

Chunk budgets and search limits are module constants in `src/synthorg/knowledge/constants.py` because they are part of the on-disk index contract. The optional `pdfplumber`, `tree-sitter`, and `tree-sitter-language-pack` dependencies ship under the `knowledge` extras group and import lazily; a missing import raises a clear dependency error with install guidance.

## Endpoints

All REST endpoints are read-only and require read access. Ingest, reindex, and delete are admin-gated and happen via MCP or agent tools, not over REST.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects/{project_id}/knowledge` | Paginated sources for a project. Query params: `cursor`, `limit` (default 50), `include_global` (default `true`), `stale_only` (default `false`). |
| `GET` | `/projects/{project_id}/knowledge/search` | Cited hits ordered by relevance. Required `q`, optional `limit` (1 to 64, default 8). |
| `GET` | `/projects/{project_id}/knowledge/ask` | Retrieves cited chunks, then synthesises a grounded answer whose every claim resolves to one of them. Same parameters as `search`. Answers `503` when no synthesis model is configured; `search` is unaffected. |
| `GET` | `/projects/{project_id}/knowledge/{source_id}` | Single source by id (404 if absent). |
| `GET` | `/knowledge` | Paginated global (project-unscoped) sources for admin UIs. |

The MCP surface (`src/synthorg/meta/mcp/domains/knowledge.py`) mirrors those reads as `synthorg_knowledge_search`, `synthorg_knowledge_ask`, `synthorg_knowledge_list`, and `synthorg_knowledge_get` (capability `knowledge:read`), and adds `synthorg_knowledge_ingest`, `synthorg_knowledge_reindex`, and `synthorg_knowledge_delete` under `knowledge:admin`, each requiring the admin guardrail arguments.

## Worked example: search a corpus

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/proj_abc123/knowledge/search?q=retry+policy&limit=5"
```

Each hit carries a relevance score, the chunk text, and a citation whose locator resolves to the exact source region. List stale sources before a re-index run:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/api/v1/projects/proj_abc123/knowledge?stale_only=true&include_global=true"
```

## Observability

The substrate emits structured log events only (no WebSocket events): `knowledge.source.ingested`, `knowledge.source.unchanged`, `knowledge.chunks.indexed`, `knowledge.reindex.started` / `.completed`, `knowledge.searched`, and `knowledge.synthesised`, plus warning-level keys for ingest failures, unresolved citations, unavailable sources, blocked ticket fetches, and invalid synthesiser output.
