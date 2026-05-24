---
title: Research Mode
description: A real research subsystem for synthetic organisations. A research brief drives query planning, multi-source retrieval (internal knowledge plus web, academic, and code search), source-credibility triage, deduplication, and citation-backed synthesis. Every run is recorded and replayable, and the deliverable's claims resolve to retrievable sources.
---

# Research Mode

Today "an agent does research" is a curl in a sandbox. Research mode replaces
that with a real pipeline: a research **brief** becomes a synthesised,
citation-backed **report** whose every claim resolves to a retrievable
source, produced through a recorded, replayable run.

## Pipeline

`ResearchService.run(brief, *, run_id, created_by)` drives six stages:

1. **Query planning** (`QueryPlanner`): decompose the brief into
   source-targeted sub-queries. Default `LlmQueryPlanner`.
2. **Multi-source retrieval** (`RetrievalSource`), fanned out concurrently
   via `asyncio.TaskGroup`: the internal knowledge substrate plus web,
   academic, and code search. A single source failing is logged and skipped;
   the run continues with the remaining candidates.
3. **Source-credibility triage** (`CredibilityTriage`): score each candidate
   and drop those below the brief's threshold. Default
   `HybridCredibilityTriage` (deterministic heuristic prefilter, then LLM
   triage on the survivors).
4. **Deduplication** (`Deduplicator`): collapse near-duplicate findings.
   Default `LexicalDeduplicator` (content-hash plus canonical-URL plus
   token-shingle Jaccard; deterministic).
5. **Synthesis** (`Synthesizer`): the LLM writes a report citing sources by
   stable reference id; `CitationBinder` validates every cited id resolves to
   a retained item. An unsourced claim raises `ResearchSynthesisError` rather
   than emitting an unverifiable report.
6. **Recording**: the run is persisted as a `ResearchRun`.

Every step is pluggable through a protocol, a default strategy, the
`build_research_service` factory, and a `ResearchConfig` discriminator
(`settings/definitions/research.py`). Safe defaults ship; web, academic, and
code retrieval use vendor-agnostic provider protocols with no bundled
implementation (mirroring `WebSearchProvider`), so a family fans out only
once a provider is injected.

## Data model

Frozen Pydantic v2 models (`research/models.py`), all `extra="forbid"`:

- `ResearchBrief` -- the input: question, project scope, source toggles,
  credibility floor, and cost / wall-clock / sub-query limits.
- `ResearchQueryPlan` / `SubQuery` -- the planner's decomposition.
- `RetrievedItem` -- one candidate, carrying a stable `ref_id`, snippet,
  content hash, relevance, and a `ResearchCitation`.
- `ResearchCitation` -- resolves a claim to a source: for `knowledge` it
  embeds the reused knowledge-substrate `Citation`; for `web` / `academic` /
  `code` it carries a typed locator.
- `SourceCredibility` -- a triage verdict.
- `ResearchClaim` -- an assertion backed by at least one citation.
- `ResearchReport` -- the deliverable: summary plus cited claims plus
  methodology counts.
- `ResearchRun` -- the persisted, replayable record: an immutable snapshot of
  the brief plus the plan, retrieved items, credibility verdicts, and report.

Identifiers are required fields, never random defaults: the agent tool and
MCP handler derive `(brief_id, run_id)` deterministically from the request,
so an identical request reproduces the same run id.

## Recording and replay

The run record is the single source of truth for retrieval. Two layers
compose to make a whole run deterministically replayable:

- **LLM calls** (planning, triage, synthesis) replay through the existing
  `CassetteCompletionProvider`.
- **Retrieval results** are persisted on the run as `retrieved_items`;
  replay swaps each `RetrievalSource` for a `ReplayRetrievalSource` that
  serves the recorded items by sub-query index. Since the plan comes from the
  cassetted planner, the same plan reproduces the same routing.

Triage's heuristic component, lexical dedup, and citation binding are
deterministic, so given the cassette plus the run record the report is
byte-stable. The default path needs no embedder.

## Persistence

A single `research_runs` table stores each run as a JSON blob with
denormalised `brief_id` / `project_id` / `status` / `created_at` columns for
filtering and ordering. `ResearchRunRepository` composes the
`IdKeyedRepository` and `FilteredQueryRepository` generics; SQLite and
Postgres implementations are conformance-tested in lockstep.

## Surfaces

- **Agent tool** `research` (`research/tool.py`): runs a brief and returns
  the cited report. Built per task by `ResearchToolFactory`.
- **MCP** domain `research:run` / `research:get` / `research:list`
  (`meta/mcp/domains/research.py`, handlers route through
  `ResearchService`), 503-ing when the service is not wired.

A REST controller and dashboard surface for operator-driven research are a
follow-up; the agent tool, MCP surface, and eval lane cover the #1989
acceptance.

## Security (SEC-1)

All retrieved external content is untrusted. Snippets are wrapped via
`wrap_untrusted(TAG_RESEARCH_SOURCE, ...)` only where they enter a prompt
(planning prompt for the brief, triage and synthesis prompts for sources),
never at storage. The synthesiser and triage system prompts carry the
untrusted-content directive. The research action is classified `research:run`
at the MEDIUM risk tier; the underlying egress is separately gated at HIGH
via `external_data:request`.

## Evaluation

A `kind="research"` eval brief carries a `ResearchBriefSpec` (question,
expected claims, credibility floor, judged rubric). `grade_research_run`
(`evals/scoring/research.py`) scores a run deterministically on claim
coverage, citation resolution (every claim citation resolves to a retrieved
source -- the acceptance criterion), and cited-source credibility. The lane
records a run, replays it, asserts the report is byte-identical, and grades
it.

## Acceptance

Given a research brief, the org produces a synthesised, citation-backed
report whose claims resolve to retrievable sources, and the run is
replayable. Validated by the research eval lane and the service-level replay
test.
