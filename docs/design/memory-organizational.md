---
title: Shared Organisational Memory
description: Company-wide knowledge accessible to every agent (policies, ADRs, conventions, procedures) behind the OrgMemoryBackend protocol.
---

# Shared Organisational Memory

Beyond individual agent memory, the framework provides **organisational memory**: company-wide
knowledge that all agents can access: policies, conventions, architecture decision records (ADRs),
coding standards, and operational procedures. This is not personal episodic memory ("what I did
last Tuesday") but institutional knowledge ("the team always uses Litestar, not Flask").

Shared organisational memory is implemented behind an `OrgMemoryBackend` protocol, making the
system modular and extensible. New backends can be added without modifying existing ones.

See also: [Memory and Persistence](memory.md) (agent memory + backend protocol), [Operational Data Persistence](memory-operational.md), [Memory Learning](memory-learning.md).

## Backend 1: Hybrid Prompt + Retrieval (Default)

Critical rules (5--10 items, e.g., "no commits to main," "all PRs need 2 approvals") are injected
into every agent's system prompt. Extended knowledge (ADRs, detailed procedures, style guides) is
stored in a queryable store and retrieved on demand at task start.

```yaml
org_memory:
  core_policies:                        # always in system prompt
    - "All code must have 80%+ test coverage"
    - "Use Litestar, not Flask"
    - "PRs require 2 approvals"
  extended_store:
    backend: "sqlite"                   # sqlite, postgresql
    max_retrieved_per_query: 5
  write_access:
    policies: ["human"]                 # only humans write core policies
    adrs: ["human", "senior", "lead", "c_suite"]
    procedures: ["human", "senior", "lead", "c_suite"]
    conventions: ["human", "senior", "lead", "c_suite"]
    entity_definitions: ["human", "senior", "lead", "c_suite"]  # ontology sync publishes at senior authority
```

`OrgMemoryBackend` stays a Protocol so future backends can substitute structurally, but the configuration discriminator is gone: `HybridPromptRetrievalBackend` is the single shipping implementation and is wired directly. New backends supply their own configuration block when they ship.

**Strengths:** Simple to implement. Core rules are always present. Extended knowledge scales
with the organisation.

**Limitations:** Basic retrieval may miss relational connections between policies.

## Research Directions

The following backends illustrate why `OrgMemoryBackend` is a protocol; the architecture
supports future upgrades without modifying existing code. These are research directions that
may inform future work if organisational memory needs outgrow the Hybrid Prompt + Retrieval
approach.

!!! info "Research Direction: GraphRAG Knowledge Graph"

    Organisational knowledge stored as entities + relationships in a knowledge graph. Agents
    query via graph traversal, enabling multi-hop reasoning: "Litestar is the standard" is
    linked to "don't use Flask," which is linked to "exception: data team uses Django for admin."

    ```yaml
    org_memory:
      backend: "graph_rag"
      graph:
        store: "sqlite"                     # graph stored in relational DB, or dedicated graph DB
        entity_extraction: "auto"           # auto-extract entities from ADRs and policies
    ```

    **Strengths:** Significant accuracy improvement over vector-only retrieval (some benchmarks
    report 3--4x gains). Multi-hop reasoning captures policy relationships.

    **Limitations:** More complex infrastructure. Entity extraction can be noisy. Heavier setup.

!!! info "Research Direction: Temporal Knowledge Graph"

    Like GraphRAG but tracks how facts change over time. "The team used Flask until March 2026,
    then switched to Litestar." Agents see current truth but can query history for context.

    ```yaml
    org_memory:
      backend: "temporal_kg"
      temporal:
        track_changes: true
        history_retention_days: null        # null = forever
    ```

    **Strengths:** Handles policy evolution naturally. Agents understand when and why things changed.

    **Limitations:** Most complex. Potentially overkill for small organisations or local-first use.

!!! tip "Upgrade Path to GraphRAG"

    If multi-hop organisational reasoning becomes a requirement (e.g., tracing which policies
    affect hiring across salary, budget, and compliance domains), the upgrade path is
    non-breaking:

    1. Add post-consolidation entity extraction as a new `EntityExtractionStrategy` in the
       consolidation pipeline (entities stored in a separate `EntityStore` protocol).
    2. Create `GraphRAGMemoryBackend` implementing the existing `OrgMemoryBackend` protocol with
       graph-traversal queries alongside standard vector retrieval.
    3. Enable via config; existing application code is unchanged.

    See [Decision Log](../architecture/decisions.md) D25 for the full trade-off analysis and
    deferral rationale.

## OrgMemoryBackend Protocol

All backends implement the `OrgMemoryBackend` protocol:

- `query(OrgMemoryQuery) -> tuple[OrgFact, ...]`
- `write(OrgFactWriteRequest, *, author: OrgFactAuthor) -> NotBlankStr`
- `list_policies() -> tuple[OrgFact, ...]`
- Lifecycle methods: `connect`, `disconnect`, `health_check`, `is_connected`, `backend_name`

The default backend is the Hybrid Prompt + Retrieval implementation. The selected memory layer
backend Mem0 ([Decision Log](../architecture/decisions.md)) provides optional graph memory via
Neo4j/FalkorDB, which could reduce implementation effort for the research direction backends.

!!! tip "Write Access Control"

    Core policies are human-only. ADRs and procedures can be written by senior+ agents. All
    writes are append-only and auditable. This prevents agents from corrupting shared organisational
    knowledge while allowing senior agents to document decisions.
