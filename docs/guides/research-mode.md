---
title: Research Mode
description: Run recorded, replayable research briefs through a six-stage pipeline that synthesises cited reports.
---

# Research Mode

Research mode replaces ad-hoc "agent does research" calls with a recorded, six-stage pipeline. A research brief (a question plus source toggles and limits) drives query planning, concurrent multi-source retrieval (internal knowledge, web, academic, code), source-credibility triage, near-duplicate deduplication, and LLM synthesis into a report whose every claim cites a retrievable source. Every run is persisted and is deterministically replayable: an identical request reproduces the same run id and swaps live retrieval for the stored items.

The implementation lives under `src/synthorg/research/`. See the [research mode design spec](../design/research-mode.md) for the full architecture.

## Concepts

- **Research brief**: the input (question, project scope, source toggles, credibility floor, sub-query ceiling).
- **Query plan**: the planner's decomposition of the brief into source-targeted sub-queries.
- **Retrieved item**: one candidate result with a stable reference id, snippet, content hash, relevance score, and citation.
- **Credibility triage**: a verdict (score, authority bucket, red flags, pass/fail) that gates retention.
- **Research report**: the deliverable (title, summary, claims, source counts, synthesis model); each claim carries at least one citation.
- **Research run**: the persisted record (brief snapshot, plan, items, verdicts, report, status, cost, elapsed time).

## Enablement

The master toggle is `research.enabled` (default `true`, hot: no restart). The service is ghost-wired at boot and live-gated per request at the MCP handlers, which return `503` while it is disabled.

Settings live in the `research` namespace (`src/synthorg/settings/definitions/research.py`); all keys are hot-reloadable (the model / strategy / threshold keys rebuild and swap the service via `ResearchSettingsSubscriber`):

| Key | Type | Default | Purpose |
|---|---|---|---|
| `research.enabled` | bool | `true` | Master switch. |
| `research.model` | str | `""` | Provider+model reference (`MODEL_REF`, carries both) for pipeline calls; empty leaves research unwired, and a non-empty value must bind both provider and model. |
| `research.query_planner` | str | `llm` | Query-planning strategy. |
| `research.credibility_triage` | str | `hybrid` | Triage strategy (`hybrid`, `heuristic`, `llm`). |
| `research.deduplicator` | str | `lexical` | Deduplication strategy (`lexical`, `embedding`). |
| `research.synthesizer` | str | `llm` | Synthesis strategy. |
| `research.triage_batch_size` | int | `10` | Items per LLM triage call (1 to 100). |
| `research.hybrid_prefilter_factor` | float | `0.6` | Heuristic fraction of the credibility floor required before LLM escalation. |
| `research.dedup_similarity_threshold` | float | `0.85` | Similarity threshold for near-duplicate collapse. |
| `research.per_query_limit` | int | `10` | Candidate items per source per sub-query. |

## Surface

Research mode has no REST controller; the operator surface is exclusively MCP (`src/synthorg/meta/mcp/domains/research.py`):

| MCP tool | Kind | Purpose |
|---|---|---|
| `synthorg_research_run` | write | Execute a brief; returns the full run including the cited report. |
| `synthorg_research_get` | read | Fetch a single run by `run_id`. |
| `synthorg_research_list` | read | List runs (recency-first), filtered by brief, project, or status. |

## Worked example: a research brief

The `synthorg_research_run` tool accepts these arguments (at least one `include_*` must be `true`):

```json
{
  "question": "What are the main scaling bottlenecks in transformer-based LLM inference?",
  "title": "LLM Inference Scaling Bottlenecks",
  "project_id": "proj-acme-ai",
  "include_knowledge": true,
  "include_web": true,
  "include_academic": true,
  "include_code": false,
  "min_credibility": 0.6,
  "max_subqueries": 6
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `question` | str | (required) | The research question (1 to 16384 chars). |
| `title` | str | first 120 chars of the question | Human-readable brief title. |
| `project_id` | str | unset | Scope knowledge retrieval to a project. |
| `include_knowledge` | bool | `true` | Query the internal knowledge substrate. |
| `include_web` | bool | `true` | Query web search. |
| `include_academic` | bool | `false` | Query academic search. |
| `include_code` | bool | `false` | Query code search. |
| `min_credibility` | float | `0.5` | Credibility floor for source retention. |
| `max_subqueries` | int | `8` | Ceiling on planner-emitted sub-queries (minimum 1, no upper bound). |

## Observability

Research mode emits structured log events only (no WebSocket events): `research.run.started`, `research.run.planned`, `research.run.retrieved`, `research.run.triaged`, `research.run.deduplicated`, `research.run.synthesised`, and `research.run.completed`, plus warning-level keys for source failures (`research.source.failed`), budget overruns (`research.budget.exceeded`), invalid LLM output (`research.llm.output_invalid`), a planner that fell back (`research.planner.fallback`), and the service being unavailable (`research.unavailable`).

The brief's identifiers are derived, not minted: `(brief_id, run_id)` is a hash over the question, project scope, source toggles, credibility floor, and sub-query ceiling, so an identical request addresses the same run and a re-run upserts rather than duplicating. That is what makes replay possible without a caller tracking ids.
