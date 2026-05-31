---
title: Memory Learning and Injection
description: Procedural memory auto-generation from failures and successes, cross-agent skill pool, memory injection strategies (context / tool-based / self-editing), and the MemoryService layer.
---

# Memory Learning and Injection

How memory enters and leaves the agent execution loop: procedural memory is
auto-generated from failed and successful executions, surfaced through one
of three injection strategies, and managed through the `MemoryService`
single entry point for REST and MCP callers.

See also: [Memory and Persistence](memory.md) (storage + retrieval pipeline), [Operational Data Persistence](memory-operational.md), [Shared Organisational Memory](memory-organizational.md).

## Procedural Memory Auto-Generation

When an agent fails a task, the engine's post-execution pipeline can automatically
generate a **procedural memory entry**: a structured "next time, do X when
encountering Y" lesson learned. This follows the
[EvoSkill](https://arxiv.org/abs/2603.02766) three-agent separation principle:
the **failed agent** does not write its own lesson; a separate **proposer LLM call**
analyses the failure.

### Pipeline

1. **Failure analysis payload** (`FailureAnalysisPayload`): Built from
   `RecoveryResult` + `ExecutionResult`. Includes task metadata, sanitized error
   message, tool calls made, retry count, and turn count. Deliberately excludes
   raw conversation messages (privacy boundary).

2. **Proposer LLM call** (`ProceduralMemoryProposer`): A separate completion
   call with its own system prompt analyses the payload and returns a structured
   `ProceduralMemoryProposal`.

3. **Three-tier progressive disclosure**:
     - **Discovery** (~100 tokens): concise summary for retrieval ranking.
     - **Activation** (condition + action + rationale): when/what/why.
     - **Execution** (ordered steps): concrete steps for applying the knowledge.

4. **Storage**: The proposal is stored via `MemoryBackend.store()` as a
   `MemoryCategory.PROCEDURAL` entry with `"non-inferable"` tag for retrieval
   filtering.

5. **SKILL.md materialization** (optional): When `ProceduralMemoryConfig.skill_md_directory`
   is set, the proposal is also written as a portable SKILL.md file following the
   [Agent Skills](https://agentskills.io/) format for git-native versioning.

### Configuration

`ProceduralMemoryConfig` (nested in `CompanyMemoryConfig.procedural`) controls:

- `enabled`: Toggle auto-generation on/off (default: `True`).
- `model`: Model identifier for the proposer LLM call (default: `"example-small-001"`).
- `temperature`: Sampling temperature (default: `0.3`).
- `max_tokens`: Token budget for the proposer response (default: `1500`).
- `min_confidence`: Discard proposals below this threshold (default: `0.5`).
- `skill_md_directory`: Optional path for SKILL.md file materialization.

### Integration Point

`AgentEngine._try_procedural_memory()` runs after error recovery in
`_post_execution_pipeline`. It is non-critical: failures are logged at WARNING
and never block the execution result.

### Capture Strategies

The capture system is extended beyond failure-only via pluggable ``CaptureStrategy``
implementations in ``memory/procedural/capture/``:

| Strategy | When it fires | Output |
|----------|--------------|--------|
| ``FailureCaptureStrategy`` | ``recovery_result is not None`` | Wraps existing proposer pipeline |
| ``SuccessCaptureStrategy`` | Successful completion with quality above threshold | ``"success-derived"`` tagged memory |
| ``HybridCaptureStrategy`` | Both failure and success paths | Delegates based on outcome |

``SuccessMemoryProposer`` (``memory/procedural/success_proposer.py``) provides a lighter
LLM analysis for successful executions, focusing on reusable strategies rather than
failure lessons.

Configuration via ``CaptureConfig``: ``type`` discriminator (``"failure"``/``"success"``/
``"hybrid"``), ``min_quality_score`` (default 8.0), ``success_quality_percentile`` (default
75.0).

### Pruning Strategies

Procedural memory pruning is handled by pluggable ``PruningStrategy`` implementations
in ``memory/procedural/pruning/``:

| Strategy | Method |
|----------|--------|
| ``TtlPruningStrategy`` | Remove entries older than ``max_age_days`` (default 90) |
| ``ParetoPruningStrategy`` | Multi-dimensional Pareto frontier (relevance + recency) down to ``max_entries`` |
| ``HybridPruningStrategy`` | TTL first (remove expired), then Pareto on remaining |

### Cross-Agent Propagation

Procedural memories can be propagated across agents via pluggable ``PropagationStrategy``
implementations in ``memory/procedural/propagation/``:

| Strategy | Scope | Tag |
|----------|-------|-----|
| ``NoPropagation`` | Agent-local only (safe default) | - |
| ``RoleScopedPropagation`` | Agents with same role | ``"propagated:{source_agent_id}"`` |
| ``DepartmentScopedPropagation`` | Agents in same department | ``"propagated:{source_agent_id}"`` |

All propagation strategies respect ``max_propagation_targets`` (default 10) and exclude
the source agent.

### Cross-Agent Skill Pool

Organisation-wide shared skills extend procedural memory with an `ORG` scope.

**`ProceduralMemoryScope` enum**: `AGENT` (per-agent private), `ROLE`,
`DEPARTMENT`, `ORG` (organisation-wide shared pool).

**Extended `ProceduralMemoryProposal`** adds fields for org-scope lifecycle:

- `scope: ProceduralMemoryScope`: distribution scope
- `supersedes: tuple[NotBlankStr, ...]`: IDs of entries this supersedes
- `superseded_by: NotBlankStr | None`: tombstone marker (filtered from retrieval)
- `application_count: int`: how many times applied
- `last_applied_at: AwareDatetime | None`: last application timestamp

**`AutonomousSkillEvolver`** runs on the consolidation schedule:

1. Collects trajectories across all agents in a window via `TrajectoryAggregator`
2. Groups by error category or tool call sequence
3. Filters patterns seen by >= `min_agents_for_pattern` distinct agents
4. Builds org-scope proposals with confidence proportional to failure rate
5. Checks supersession against existing org entries (FULL/PARTIAL/CONFLICT)
6. Emits proposals as `ApprovalItem` entries for human review

**Proposal-only, structurally enforced**: `EvolverConfig.requires_human_approval`
is `Literal[True]` and cannot be set to `False`. The evolver has no write access
to org memory. Proposals land in the existing `ApprovalItem` queue.

**Supersession rules** (checked before proposal emission):

| Verdict | Condition | Action |
|---------|-----------|--------|
| CONFLICT | High condition overlap + low action similarity | Skipped, escalated to human |
| FULL | Condition superset + compatible action + higher confidence | Supersedes existing (post-approval) |
| PARTIAL | Everything else | Both coexist |

CONFLICT is checked before FULL to prevent contradictory actions from
being accepted as supersessions.

**`EvolverConfig` safety rails**: `enabled` (default False, opt-in),
`min_confidence_for_org_promotion` (0.8), `min_agents_seen_pattern` (3),
`max_proposals_per_cycle` (10), `max_org_entries` (10000, reserved for
future pruning).

**Observability**: `SKILL_EVOLVER_CYCLE_START`, `SKILL_EVOLVER_CYCLE_COMPLETE`,
`SKILL_EVOLVER_CYCLE_FAILED`, `SKILL_EVOLVER_PROPOSAL_EMITTED`,
`SKILL_EVOLVER_CONFLICT_DETECTED`, `ORG_SKILL_SUPERSEDED`, `SKILL_EVOLVER_DISABLED`.

`EvolverReport` is consumed by R3 #1265 eval loop.

---

## Memory Injection Strategies

Agent memory reaches agents through pluggable injection strategies behind the
`MemoryInjectionStrategy` protocol. The strategy determines *how* memories are surfaced to
the agent during execution.

=== "Context Injection (Default)"

    Pre-retrieves relevant memories before execution, ranks by relevance and recency, enforces
    a token budget, and formats memories as `ChatMessage`(s) injected between the system prompt
    and task instruction. The agent passively receives memories.

    **Pipeline (Linear, single-source, default):**

    1. `MemoryBackend.retrieve()`: fetch candidate memories (dense vector search)
    2. Rank by relevance + recency via linear combination
    3. Filter by `min_relevance` threshold
    4. Apply `MemoryFilterStrategy` ([Decision Log](../architecture/decisions.md) D23, optional): exclude inferable content (fails **closed** on filter exceptions: returns empty to avoid bypassing privacy filters)
    5. **Optional MMR diversity re-ranking** when `diversity_penalty_enabled: true`,
       balancing relevance vs redundancy via Maximal Marginal Relevance with
       word-bigram Jaccard similarity (see **Diversity Re-ranking** below).
       Filtering runs first so excluded entries do not act as MMR anchors and
       suppress diverse-but-visible candidates.
    6. Greedy token-budget packing
    7. Format as `ChatMessage` (configured role: SYSTEM or USER) with delimiters

    **Pipeline (RRF hybrid search, multi-source):**

    When `fusion_strategy: rrf` is configured, the pipeline runs both dense and BM25 sparse
    search in parallel and fuses results:

    1. Dense search: `MemoryBackend.retrieve()` for personal, `SharedKnowledgeStore.search_shared()` for shared (in parallel)
    2. Sparse BM25 search: `MemoryBackend.retrieve_sparse()` for personal (shared sparse disabled until `SharedKnowledgeStore` adds the method)
    3. Fuse via `fuse_ranked_lists()` with configurable `rrf_k` smoothing constant
    4. Post-RRF `min_relevance` filter on `combined_score`
    5. Apply `MemoryFilterStrategy` (optional, fails closed)
    6. **Optional MMR diversity re-ranking** when `diversity_penalty_enabled: true`
    7. Greedy token-budget packing
    8. Format as `ChatMessage`

    BM25 sparse vectors are stored alongside dense vectors in Qdrant using a named sparse
    vector field with `Modifier.IDF` (Qdrant applies IDF server-side). The `BM25Tokenizer`
    uses murmurhash3 for vocabulary-free token-to-index mapping; only term frequencies are
    stored. Sparse search is opt-in via `Mem0BackendConfig.sparse_search_enabled`.

    Shared memories (from `SharedKnowledgeStore`) are fetched in parallel, merged with personal
    memories (no `personal_boost` for shared), and ranked together.

    **Ranking Algorithm (Linear, default):**

    1. `relevance = entry.relevance_score ?? config.default_relevance`
    2. Personal entries: `relevance = min(relevance + personal_boost, 1.0)`
    3. `recency = exp(-decay_rate * age_hours)`
    4. `combined = relevance_weight * relevance + recency_weight * recency`
    5. Filter: `combined >= min_relevance`
    6. Sort descending by `combined_score`

    **Alternative: Reciprocal Rank Fusion (RRF)**

    When `fusion_strategy: rrf` is configured, multiple pre-ranked lists (e.g., from different
    retrieval sources) are merged via RRF: `score(doc) = sum(1 / (k + rank_i))` across all
    lists containing the document. Scores are min-max normalised to [0.0, 1.0]. The smoothing
    constant `k` (default 60, configurable via `rrf_k`) controls rank-difference amplification.
    RRF is the de facto standard for hybrid search fusion
    ([Qdrant](https://qdrant.tech/articles/hybrid-search/),
    [NeMo Retriever](https://huggingface.co/blog/nvidia/nemo-retriever-agentic-retrieval)). It is
    intended for multi-source scenarios (BM25 + vector, multi-round tool-based retrieval); the
    linear strategy remains the default for single-source retrieval. Results are truncated to
    `max_results` (default 20) after scoring and sorting.

    **Diversity Re-ranking (MMR)**

    When `diversity_penalty_enabled: true` is set on the config, the
    `ContextInjectionStrategy` pipeline runs `apply_diversity_penalty()` after
    filtering and before token-budget packing. Running the filter first ensures
    that privacy-excluded entries are not used as MMR anchors (which could
    otherwise suppress visible candidates that happen to be textually similar to
    excluded ones).  The re-ranker uses Maximal Marginal Relevance:

        MMR(candidate) = lambda * combined_score - (1 - lambda) * max_sim_to_selected

    where `diversity_lambda` (default 0.7, range `[0.0, 1.0]`) controls the
    trade-off: `1.0` = pure relevance (no diversity penalty), `0.0` = maximum
    diversity. The default similarity function is word-bigram Jaccard; callers
    can inject a custom `similarity_fn` (e.g., cosine on embeddings) for
    domain-specific redundancy measures. Bigram sets are pre-computed once per
    entry to keep complexity at `O(n**2)` rather than `O(n**2 * k)`.  When
    diversity is enabled, the backend over-fetches by a configurable
    `candidate_pool_multiplier` (default 3x, range 1--10) so MMR can promote
    diverse candidates that would otherwise fall below the top-K cutoff. This
    feature applies only to `ContextInjectionStrategy`; a `model_validator`
    warns when `diversity_penalty_enabled=True` is combined with a strategy
    that ignores it (e.g. `TOOL_BASED`).

    !!! tip "Non-Inferable Filter"

        Retrieved memories are filtered before injection to exclude content the agent can
        discover by reading the codebase or environment. Only non-inferable information is
        injected: prior decisions, learned conventions, interpersonal context, historical
        outcomes. [Research](https://arxiv.org/abs/2602.11988) shows generic context increases
        cost 20%+ with minimal success improvement; LLM-generated context can actually reduce
        success rates.

        **Filter strategy ([Decision Log](../architecture/decisions.md) D23):** Pluggable `MemoryFilterStrategy` protocol. Initial
        implementation uses tag-based filtering at write time. A `non-inferable` tag convention
        with advisory validation at the `MemoryBackend.store()` boundary warns on missing tags
        but never blocks. The system prompt instructs agents what qualifies as non-inferable:
        design rationale, team decisions, "why not X," cross-repo knowledge. Uses existing
        `MemoryMetadata.tags` and `MemoryQuery.tags`; zero new models needed.

=== "Tool-Based Retrieval"

    The agent has `recall_memory` / `search_memory` tools it calls on-demand during execution.
    The agent actively decides when and what to remember. More token-efficient (only retrieves
    when needed) but consumes tool-call turns and requires agent discipline to invoke.

    Implemented via `ToolBasedInjectionStrategy`. The strategy:

    - Injects a brief system instruction about available memory tools
    - Exposes `search_memory` and `recall_memory` (by ID) tools
    - Delegates `search_memory` requests to `MemoryBackend.retrieve()` (dense-only;
      hybrid dense+sparse with RRF fusion is not yet wired into the tool-based path)
    - Hybrid retrieval and RRF fusion are handled at the `ContextInjectionStrategy`
      level, not within `ToolBasedInjectionStrategy`
    - When `query_reformulation_enabled: true` is set on the config and both a
      `QueryReformulator` and a `SufficiencyChecker` are provided at construction,
      `search_memory` runs an iterative **Search-and-Ask** loop: retrieve -> check
      sufficiency -> reformulate query -> re-retrieve, up to `max_reformulation_rounds`
      rounds (default 2, max 5).  Results from all rounds are merged by entry ID,
      keeping the highest-relevance version of any duplicate. Sufficiency checker
      and reformulator failures degrade gracefully to the current cumulative entries
      rather than propagating. Diversity (MMR) re-ranking is applied only
      in the `ContextInjectionStrategy` pipeline, not in the tool-based handler.

    **ToolRegistry integration**: `SearchMemoryTool` and `RecallMemoryTool` are `BaseTool`
    subclasses (defined in the `memory/tools/` package) that delegate execution to
    `ToolBasedInjectionStrategy.handle_tool_call()`.  The `registry_with_memory_tools()`
    factory augments a `ToolRegistry` with these tools when the strategy is
    `ToolBasedInjectionStrategy`.  `AgentEngine` accepts an optional
    `memory_injection_strategy` parameter and wires the tools into each agent's registry
    at execution time. This ensures memory tools participate in the standard `ToolInvoker`
    dispatch pipeline, including permission checking (`ToolCategory.MEMORY`), security
    interceptors, and invocation tracking.

    **MCP bridge evaluation**: Both context injection and tool-based strategies hold direct
    `MemoryBackend` references and run in-process. The memory hot path bypasses MCP by design;
    no additional optimisation needed.

=== "Self-Editing Memory"

    The agent has three structured memory blocks (core, archival, and recall) it reads AND
    writes during execution via dedicated tools. Core memory (SEMANTIC category, tagged ``"core"``)
    is always injected into the system prompt. Archival and recall memories are tool-searched on
    demand. Six tools are provided: ``core_memory_read``, ``core_memory_write``,
    ``archival_memory_search``, ``archival_memory_write``, ``recall_memory_read``,
    ``recall_memory_write``.

    Implemented via ``SelfEditingMemoryStrategy``. Token overhead is ~250--650 tokens per session
    (2--10 writes + 5--15 searches). Best suited for long-running, high-autonomy agents (>20 turns)
    where explicit memory management reduces "forgotten context" errors. ``SelfEditingMemoryConfig``
    controls core token budget, archival search limit, per-category write access, and a safety
    valve (``allow_core_writes: bool``) for restricting core memory edits on locked-down agents.

### MemoryInjectionStrategy Protocol

All strategies implement `MemoryInjectionStrategy`:

```python
class MemoryInjectionStrategy(Protocol):

    async def prepare_messages(
        self, agent_id: NotBlankStr, query_text: NotBlankStr, token_budget: int
    ) -> tuple[ChatMessage, ...]: ...

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]: ...

    @property
    def strategy_name(self) -> str: ...
```

Strategy selection via config: ``memory.retrieval.strategy: context | tool_based | self_editing``

---

## Memory Service Layer

`MemoryService` (at `src/synthorg/memory/service.py`) is the single entry point for `/memory/fine-tune/*` REST endpoints and the MCP memory tools. Controllers and handlers never reach into `app_state.persistence.*` directly; the service owns the repository handle, audit logging, and typed error routing.

### Fine-tune lifecycle

`MemoryService` exposes the full fine-tune lifecycle as typed async methods:

- `start_fine_tune(plan: FineTunePlan) -> FineTuneRun`: starts a new pipeline from a `FineTunePlan`.
- `resume_fine_tune(run_id: NotBlankStr) -> FineTuneRun`: resume a previously failed or cancelled run.
- `get_fine_tune_status(run_id: NotBlankStr | None = None) -> FineTuneStatus`: snapshot of the active (or a specific) run.
- `cancel_fine_tune() -> str | None`: cancel the active run (destructive). Returns the cancelled run id (captured **before** cancel so the audit log can attribute it) or `None` if no run was active.
- `run_preflight(plan: FineTunePlan) -> PreflightResult`: local-env sanity check (source dir, output dir writability, override bounds).
- `list_runs(*, limit: int, offset: int) -> tuple[tuple[FineTuneRun, ...], int]`: paged historical runs + total count.
- `get_active_embedder() -> ActiveEmbedderSnapshot`: frozen snapshot of the active provider / model / checkpoint id from settings.
- `rollback_checkpoint(checkpoint_id: NotBlankStr) -> CheckpointRecord`: atomic swap of the active embedder back to *checkpoint_id* (destructive). The rollback-step helper logs a distinct `MEMORY_CHECKPOINT_ROLLBACK_FAILED` event if any intermediate step fails so operators can distinguish partial-rollback from the primary deploy failure.

Destructive entries (`cancel_fine_tune`, `rollback_checkpoint`, and `delete_checkpoint` at the handler layer) are gated by the standard MCP guardrail triple (`actor`, literal `confirm=True`, non-blank `reason`) and emit `MCP_ADMIN_OP_EXECUTED` with the resolved actor, reason, and `target_id` (the cancelled run id or the rolled-back / deleted checkpoint id).

`FineTunePlan` is an MCP-facing Pydantic model (`src/synthorg/memory/fine_tune_plan.py`) that mirrors the runner's internal `FineTuneRequest` and isolates the public contract from runner internals. A `@model_validator` rejects parent-directory traversal, backslashes, and Windows drive letters on `source_dir` / `output_dir` before the runner's subprocess or container mount could expose the host filesystem.

### Training data sources (directory vs trajectory)

The fine-tune pipeline draws its `{query, positive_passage}` contrastive pairs from one of two sources, selected per run via `FineTuneRequest.data_source` (`FineTuneDataSourceType`, default `directory`):

- **`directory`** (default): scans a static document directory (`source_dir`), the original behaviour. A cross-field validator requires `source_dir` in this mode.
- **`trajectory`**: harvests the organisation's real working history through a `TrajectoryTrainingDataSource` (`src/synthorg/memory/embedding/training_sources.py`), so no `source_dir` is needed. It draws three passage sources off the completed/failed-task spine and pairs each passage with the originating task title as the retrieval query:
  - **Accepted deliverables**: artifacts of COMPLETED tasks via `ArtifactRepository`.
  - **Distillation trajectories**: EPISODIC distillation entries (condensed run narratives).
  - **Corrected failures**: PROCEDURAL `failure:*` lessons from the procedural-memory pipeline.

  Pairs are then **curated by benchmark score**: the golden-company scorecard history is the quality filter. A record is kept only when the benchmark run that first observed it (the earliest run at or after the record's timestamp) passed; records newer than the latest run inherit that run's verdict. With no benchmark history every pair is kept.

The trajectory source is a REST opt-in: the MCP `FineTunePlan` keeps `source_dir` required and always runs in directory mode, so trajectory harvesting is an explicit dashboard / REST choice rather than a silent default that would break an empty organisation or an existing directory-mode caller.

### Checkpoint promotion gate

A fine-tuned checkpoint replaces the active embedder **only on a measured win**. After training, the candidate is A/B'd against the current embedder on the retrieval benchmark, and `should_promote(base_score, candidate_score, *, margin)` (`src/synthorg/memory/embedding/promotion.py`) returns `True` only when `candidate_score - base_score >= margin` (default `0.01`, strictly positive so a tie never promotes). On a win the orchestrator deploys the new checkpoint and deactivates the rest; on a tie or loss it records the checkpoint inactive and logs `MEMORY_FINE_TUNE_CHECKPOINT_REJECTED`. The gate is a pure, signal-agnostic function and the orchestrator is the single policy point; `deploy_checkpoint` stays a mechanism (no fail-close) so a rollback can always reactivate a prior checkpoint.

### Startup wiring

The `FineTuneOrchestrator` is wired on startup by `_wire_fine_tune_orchestrator` (`src/synthorg/api/lifecycle_helpers/feature_wiring.py`) once a persistence backend that exposes the fine-tune repositories is connected; a backend without fine-tune support leaves the controllers at 501. When a memory backend is also present the orchestrator receives a `TrajectoryTrainingDataSource` so trajectory-mode runs can harvest real history; without one, trajectory mode is unavailable and directory mode still works. On wiring the orchestrator recovers any run interrupted by a prior crash (marking it `FAILED`). The wire is best-effort and idempotent: a failure degrades the controllers to 501 rather than poisoning startup.

### BackendUnsupportedError routing

Fine-tune orchestration is SQLite-backed. On a persistence backend that does not expose `fine_tune_runs` / `fine_tune_checkpoints`, the service raises a typed `BackendUnsupportedError` (`domain_code = "not_supported"`, frozen with `__slots__ = ("reason",)`) instead of a generic `NotImplementedError`. MCP handlers catch it and forward through the standard `not_supported()` envelope, which emits `MCP_HANDLER_NOT_IMPLEMENTED` at WARNING; distinct from `MCP_HANDLER_CAPABILITY_GAP` (handler wired, primitive method missing) and `MCP_HANDLER_SERVICE_FALLBACK` (legacy helper, zero call sites). REST controllers map it to HTTP 501 with the same domain code.

The typed error keeps the "which gap" question resolvable without string-matching exception messages: backend-unsupported is always exactly one error class and one emitted event.
