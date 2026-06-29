---
title: LLM Call Analytics and Coordination Metrics
description: Per-call tracking, call categorisation, the orchestration ratio, the coordination metrics suite, and the coordination error taxonomy.
---

# LLM Call Analytics and Coordination Metrics

Every LLM provider call is tracked with comprehensive metadata for financial reporting, debugging, and orchestration overhead analysis. These analytics drive the multi-agent tuning signals (orchestration ratio, coordination efficiency, error amplification) that complement the [budget and cost controls](budget.md).

## Per-Call Tracking and Proxy Overhead Metrics

Every completion call produces a `CompletionResponse` with `TokenUsage` (token counts and cost). The engine layer creates a `CostRecord` (with agent/task context) and records it into `CostTracker`. The engine additionally logs **proxy overhead metrics** at task completion:

- `turns_per_task`: number of LLM turns to complete the task
- `tokens_per_task`: total tokens consumed
- `cost_per_task`: total cost in configured currency
- `duration_seconds`: wall-clock execution time
- `prompt_tokens`: estimated system prompt tokens
- `prompt_token_ratio`: ratio of prompt tokens to total tokens (overhead indicator; warns when >0.3)

These are natural overhead indicators; a task consuming 15 turns and 50k tokens for a one-line fix signals a problem. Metrics are captured in `TaskCompletionMetrics`, a frozen Pydantic model with a `from_run_result()` factory method.

## Call Categorisation and Orchestration Ratio

When multi-agent coordination exists, each `CostRecord` is tagged with a **call category**:

| Category | Description | Examples |
|----------|-------------|---------|
| `productive` | Direct task work: tool calls, code generation, task output | Agent writing code, running tests |
| `coordination` | Inter-agent communication: delegation, reviews, meetings | Manager reviewing work, agent presenting in meeting |
| `system` | Framework overhead: system prompt injection, context loading | Initial prompt, [memory retrieval injection](memory-learning.md#memory-injection-strategies) |
| `embedding` | Embedding model calls: memory store/retrieve vectorization | Mem0 store embedding, similarity search query embedding |

The **orchestration ratio** (`coordination / total`) is surfaced in metrics and alerts. If coordination tokens consistently exceed productive tokens, the company configuration needs tuning (fewer approval layers, simpler [meeting protocols](communication-coordination.md#meeting-protocol), etc.).

???+ note "Coordination Metrics Suite"

    A comprehensive suite of coordination metrics derived from empirical agent scaling research
    ([Kim et al., 2025](https://arxiv.org/abs/2512.08296)). These metrics explain coordination
    dynamics and enable data-driven tuning of multi-agent configurations.

    | Metric | Symbol | Definition | What It Signals |
    |--------|--------|------------|-----------------|
    | **Coordination efficiency** | `Ec` | `success_rate / (turns / turns_sas)`: success normalised by relative turn count vs single-agent baseline | Overall coordination ROI. Low Ec = coordination costs exceed benefits |
    | **Coordination overhead** | `O%` | `(turns_mas - turns_sas) / turns_sas * 100%`: relative turn increase | Communication cost. Optimal band: 200--300%. Above 400% = over-coordination |
    | **Error amplification** | `Ae` | `error_rate_mas / error_rate_sas`: relative failure probability | Whether MAS corrects or propagates errors. Centralised ~4.4x, Independent ~17.2x |
    | **Message density** | `c` | Inter-agent messages per reasoning turn | Communication intensity. Performance saturates at ~0.39 messages/turn |
    | **Redundancy rate** | `R` | Mean cosine similarity of agent output embeddings | Agent agreement. Optimal at ~0.41 (balances fusion with independence) |
    | **Amdahl ceiling** | `Sc` | Theoretical max speedup from Amdahl's Law given parallelizable fraction | Diminishing returns threshold. Recommends ideal team size |
    | **Straggler gap** | `Gs` | `(slowest_turn - median_turn) / median_turn` | Bottleneck severity. High gap = one agent blocks the group |
    | **Token-speedup ratio** | `Rt` | `total_tokens / speedup_factor` | Cost efficiency of parallelism. Rising ratio = diminishing token ROI |
    | **Message overhead** | `Mo` | Pairwise message count relative to team size | Quadratic communication detection. `is_quadratic` flag when `O(n^2)` |

    All 9 metrics are opt-in via `coordination_metrics.enabled` in analytics config. `Ec` and
    `O%` are cheap (turn counting). `Ae` requires baseline comparison data. `R` requires
    semantic analysis of agent outputs (embedding cosine similarity). `c`, `Sc`, `Gs`, `Rt`,
    and `Mo` are computed from execution telemetry (turn counts, token usage, message logs).

    ```yaml
    coordination_metrics:
      enabled: false                       # opt-in -- enable for data gathering
      collect:
        - efficiency                       # cheap -- turn counting
        - overhead                         # cheap -- turn counting
        - error_amplification              # requires SAS baseline data
        - message_density                  # requires message counting infrastructure
        - redundancy                       # requires embedding computation on outputs
        - amdahl_ceiling                   # computed from parallelizable fraction
        - straggler_gap                    # computed from per-agent turn times
        - token_speedup_ratio              # computed from token usage + speedup
        - message_overhead                 # computed from pairwise message counts
      error_taxonomy:
        enabled: false                     # opt-in -- enable for targeted diagnosis
        categories:
          - logical_contradiction
          - numerical_drift
          - context_omission
          - coordination_failure
    ```

    The `Ae` baseline is a sliding window of recent single-agent (SAS)
    runs. Its size is the `budget.baseline_window_size` setting
    (default 50), sourced from the `SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE`
    environment variable at API start. It is read-only post-init: the
    window is sized once when the baseline store is constructed, so a
    change requires a restart.

???+ note "Full Analytics Layer Configuration"

    Expanded per-call metadata for comprehensive financial and operational reporting:

    ```yaml
    call_analytics:
      track:
        - call_category                    # productive, coordination, system, embedding
        - success                          # true/false
        - retry_count                      # 0 = first attempt succeeded
        - retry_reason                     # rate_limit, timeout, internal_error
        - latency_ms                       # wall-clock time for the call
        - finish_reason                    # stop, tool_use, max_tokens, error
        - cache_hit                        # prompt caching hit/miss (provider-dependent)
      aggregation:
        - per_agent_daily                  # agent spending over time
        - per_task                         # total cost per task
        - per_department                   # department-level rollups
        - per_provider                     # provider reliability and cost comparison
        - orchestration_ratio              # coordination vs productive tokens
      alerts:
        orchestration_alerts:
          info: 0.30                       # info if coordination > 30% of total
          warn: 0.50                       # warn if coordination > 50% of total
          critical: 0.70                   # critical if coordination > 70% of total
        retry_alerts:
          warn_rate: 0.1                   # warn if > 10% of calls need retries
        prompt_class_alerts:               # per-prompt-purpose ceilings (opt-in)
          cost_warn: null                  # budget-currency total-cost ceiling, or null
          p95_latency_warn_ms: null        # p95 latency ceiling in ms, or null
          min_seconds_between_alerts: 300  # re-alert throttle window per purpose
    ```

    The `alerts` keys mirror the `CallAnalyticsConfig` sub-models
    (`orchestration_alerts`, `retry_alerts`, `prompt_class_alerts`). A
    per-purpose alert is opt-in: with both ceilings `null` no notification
    fires, and a purpose re-alerts at most once per
    `min_seconds_between_alerts` so a polled dashboard cannot storm the sinks.

    Analytics metadata is append-only and never blocks execution. Failed analytics writes are
    logged and skipped; the agent's task is never delayed by telemetry.

## Coordination Error Taxonomy

When coordination metrics collection is enabled, the system classifies coordination errors into structured categories for targeted diagnosis. Each category supports a pluggable dual-implementation system: a cheap heuristic variant (regex/structural) and an optional LLM-backed semantic variant (accurate, expensive, disabled by default).

**Detection scope**: detectors operate at SAME_TASK (single execution) or TASK_TREE (parent + delegate executions via `parent_task_id` linkage). Cross-agent data is sanitized via `sanitize_message` before inclusion.

| Error Category | Description | Heuristic Variant | Semantic Variant | Default Scope |
|---------------|-------------|-------------------|------------------|--------------|
| **Logical contradiction** | Agent asserts both "X is true" and "X is false" | Regex assertion matching | LLM reasoning over assistant texts | SAME_TASK |
| **Numerical drift** | Accumulated errors from cascading rounding (>5% deviation) | Context-labelled number extraction + % drift | LLM cross-verification of numerical claims | SAME_TASK |
| **Context omission** | Failure to reference previously established entities | Capitalized entity set diff (first-half/second-half) | LLM entity introduction/disposition tracking | SAME_TASK |
| **Coordination failure** | Message misinterpretation, task allocation conflicts | Tool errors + error finish reasons | LLM classification of coordination breakdowns | TASK_TREE |
| **Delegation protocol violation** | Broken delegation chains, missing parent linkage | Structural check: parent_task_id, delegation_chain integrity | - | TASK_TREE |
| **Review pipeline violation** | PASS without stages, PASS contradicting FAIL stage | Structural check: verdict/stage consistency | - | TASK_TREE |
| **Authority breach attempt** | Execution cost exceeding authority budget limit | Budget comparison: total turn cost vs limit | - | SAME_TASK |

**Pipeline architecture**: detectors implement the `Detector` protocol and are discovered dynamically from `ErrorTaxonomyConfig.detectors` (a dict mapping `ErrorCategory` to per-category variant/scope config). When multiple variants target the same category, a `CompositeDetector` runs them concurrently and deduplicates findings by `(turn_range, description_hash, category)`.

**Downstream sinks**: `ClassificationSink` protocol enables wiring findings into the performance tracker (`PerformanceTrackerSink`) and notification dispatcher (`NotificationDispatcherSink`, threshold-filtered).

**Cost control**: LLM semantic variants share the provider's rate limiter and track per-classification-run cost against `classification_budget_per_task`.

Error taxonomy classification runs post-execution (never blocks agent work) and logs structured events to the observability layer. Enable via `coordination_metrics.error_taxonomy.enabled: true`.

Error categories derived from [Kim et al., 2025](https://arxiv.org/abs/2512.08296) and the Multi-Agent System Failure Taxonomy (MAST) by Cemri et al. (2025).
