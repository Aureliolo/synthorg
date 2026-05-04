# Scoring hyperparameters

This page tracks the operator-tunable scoring weights and thresholds
across the SynthOrg engine. Each entry lists the **current default**,
the **setting that controls it**, and a **rationale** for the value.
Where rationale reads "audit-set placeholder", the value carries no
validated empirical derivation; it ships as a starting point and is
subject to revision when an evaluation infrastructure for the
relevant scoring path is in place.

This document is **tracking-only**. Hyperparameter improvements come
from manual bug fixes, architectural changes, and prompt engineering;
not from auto-tuning sweeps. See issue #1739 for the convention rule
that lifted these values from bare numeric literals into settings.

## Routing scorer (`AgentTaskScorer`)

The agent-task routing scorer assigns a 0-1 fitness score to every
candidate agent for a given subtask. Sum of weights + bonuses is
1.1 with the tag bonus; the caller caps at 1.0.

| Setting | Default | Controls |
|---|---:|---|
| `engine.routing.weight_primary_skill` | 0.4 | Weight on primary-skill overlap component. |
| `engine.routing.weight_secondary_skill` | 0.2 | Weight on secondary-skill overlap (excluding primary matches). |
| `engine.routing.weight_tag_match_bonus` | 0.1 | Bonus when every required tag is covered by matched skills. |
| `engine.routing.weight_role_match_bonus` | 0.2 | Bonus on role-name match (case-insensitive). |
| `engine.routing.weight_seniority_alignment_bonus` | 0.2 | Bonus on seniority/complexity alignment. |
| `engine.routing.min_score` | 0.1 | Minimum viable candidate score; below filters out before ranking. |

**Rationale.** Audit-set placeholders calibrated so primary-skill
overlap dominates (0.4) while role and seniority each independently
push fit by 0.2. The tag bonus at 0.1 keeps tag-match a tiebreaker
rather than a primary axis. `min_score=0.1` filters out candidates
that score on seniority alone (matches the seniority bonus). No
empirical derivation; revisit when routing-decision telemetry is in
place.

## Model matcher (`match_model`)

Selects the best provider-model fit for a tier-bound `ModelRequirement`.
Three score components: tier base + headroom + priority alignment.

| Setting | Default | Controls |
|---|---:|---|
| `engine.matcher.tier_base_score` | 0.5 | Floor when a model satisfies the tier. |
| `engine.matcher.headroom_max_bonus` | 0.25 | Max bonus when context window comfortably exceeds the requirement. |
| `engine.matcher.priority_max_bonus` | 0.25 | Max bonus from priority-axis ranking (cost/quality/speed). |
| `engine.matcher.headroom_ratio_cap` | 2.0 | Maximum context-headroom multiple credited. |
| `engine.matcher.balanced_partial_credit` | 0.125 | Bonus for the balanced-priority "no preference" fallback. |

**Rationale.** Audit-set placeholders chosen so tier match alone
gives 0.5, headroom adds up to 0.25, and priority alignment adds up
to 0.25. The 2.0 ratio cap means a model with twice the requested
context gets the full headroom bonus; beyond that, more headroom is
wasted on the priority axis. Balanced partial credit at 0.125 is half
of `priority_max_bonus`. No empirical derivation; revisit alongside
matcher-quality telemetry.

## Heuristic quality grader (`HeuristicRubricGrader`)

Rule-based fallback grader used when no LLM grader is configured.
Grades probes by checking whether each criterion's source text appears
in the artifact payload (case-insensitive).

| Setting | Default | Controls |
|---|---:|---|
| `engine.quality.heuristic.pass_threshold` | 0.5 | Probe-pass-ratio threshold; ratio greater than or equal to this value earns the PASS verdict. |
| `engine.quality.heuristic.pass_grade` | 0.8 | Per-criterion grade on pass. |
| `engine.quality.heuristic.fail_grade` | 0.3 | Per-criterion grade on fail. |
| `engine.quality.heuristic.confidence_ceiling` | 0.9 | Maximum reported confidence. |
| `engine.quality.heuristic.confidence_bias` | 0.1 | Additive bias on derived confidence (prevents 0%). |

**Rationale.** Audit-set placeholders. Pass threshold of 0.5 means
"at least half the probes match". Pass/fail grades of 0.8/0.3 give a
clean PASS-vs-FAIL split that downstream consumers can threshold
against. Confidence ceiling 0.9 acknowledges the heuristic is
deterministic but not authoritative; bias 0.1 ensures every grading
returns at least some confidence. Revisit when LLM-graded
evaluations create comparison ground-truth.

## Default client feedback (`_build_default_client`)

Synthetic feedback profile attached to default `AIClient` instances.
The strictness multiplier scales a profile's `strictness_level` onto
the 0-1 acceptance curve.

| Setting | Default | Controls |
|---|---:|---|
| `client.scored_feedback.passing_score` | 0.5 | Default passing-score threshold. |
| `client.scored_feedback.strictness_multiplier` | 2.0 | Multiplier on profile strictness for acceptance sensitivity. |
| `client.scored_feedback.strictness_floor` | 0.1 | Floor on the multiplier (keeps strictness=0 from disabling feedback). |

**Rationale.** Audit-set placeholders. Passing score of 0.5 sits at
the midpoint, treating exactly-half-correct interactions as the
boundary. Strictness multiplier of 2.0 means a profile with
`strictness_level=0.5` produces an effective multiplier of 1.0 (the
"neutral" point); `strictness_level=1.0` doubles sensitivity. The
0.1 floor keeps the multiplier non-zero so feedback weighting never
collapses entirely. No empirical derivation; revisit when client
simulation calibration data is available.

## Limit / timeout settings (cluster #28)

Operator-tunable concurrency, retry, and shutdown budgets. The
consumer modules (rate-limit stores, CAS retry handler, bus bridge,
dispatcher, lifecycle stop sequence, fine-tune chunker) keep
module-level constants that mirror these defaults so a service
constructed without an explicit settings handle still observes the
documented behaviour. Each fallback constant carries a
`# lint-allow: magic-numbers -- bootstrap fallback for <yaml_path>`
marker pointing at the canonical setting.

| Setting | Default | Controls |
|---|---:|---|
| `api.rate_limit.gc_every_n_acquires` | 1024 | Sliding-window limiter: acquires between cold-bucket GC sweeps. |
| `api.rate_limit.gc_min_horizon_seconds` | 60 | Sliding-window limiter: floor on the cold-bucket eviction horizon. |
| `api.rate_limit.inflight_gc_every_n_acquires` | 1024 | Inflight (per-op concurrency) limiter: acquires between GC sweeps. |
| `api.rate_limit.inflight_min_retry_after_seconds` | 1 | Inflight limiter: minimum `Retry-After` value emitted on 429. |
| `coordination.cas.max_retries` | 2 | CAS retry budget for optimistic-concurrency mutations. |
| `communication.bus_bridge.max_consecutive_errors` | 30 | Bus bridge poll-loop error budget before back-off escalates. |
| `communication.bus_bridge.drain_timeout_seconds` | 10.0 | Bus bridge `stop()` drain hard deadline. |
| `workers.dispatcher.publish_max_attempts` | 3 | Task-claim publish retry budget. |
| `workers.dispatcher.publish_backoff_base_seconds` | 0.1 | Dispatcher exponential-backoff base. |
| `workers.dispatcher.publish_backoff_cap_seconds` | 1.0 | Dispatcher backoff per-attempt ceiling. |
| `memory.fine_tune.vram_batch_table` | `[[40,128],[16,64],[8,32]]` | VRAM-to-batch-size mapping for embedding fine-tune preflight. |
| `memory.fine_tune.chunk_size` | 512 | Word-chunk size for synthetic-data generation. |
| `communication.loop_prevention_window_seconds` | 60.0 | Per-pair delegation rate-limit window. |
| `api.lifecycle.task_engine_shutdown_seconds` | 8.0 | Lifecycle stop step deadline (task engine). |
| `api.lifecycle.meeting_scheduler_shutdown_seconds` | 2.0 | Lifecycle stop step deadline (meeting scheduler). |
| `api.lifecycle.performance_tracker_shutdown_seconds` | 2.0 | Lifecycle stop step deadline (performance tracker). |
| `api.lifecycle.backup_shutdown_seconds` | 5.0 | Lifecycle stop step deadline (backup service). |
| `api.lifecycle.settings_dispatcher_shutdown_seconds` | 2.0 | Lifecycle stop step deadline (settings dispatcher). |
| `api.lifecycle.bridge_shutdown_seconds` | 2.0 | Lifecycle stop step deadline (bus / webhook bridge, per bridge). |
| `api.lifecycle.distributed_queue_shutdown_seconds` | 3.0 | Lifecycle stop step deadline (JetStream distributed queue). |
| `api.lifecycle.message_bus_shutdown_seconds` | 3.0 | Lifecycle stop step deadline (in-process message bus). |
| `api.lifecycle.persistence_shutdown_seconds` | 5.0 | Lifecycle stop step deadline (persistence backend drain + checkpoint). |
| `api.lifecycle.approval_timeout_shutdown_seconds` | 1.0 | Lifecycle stop step deadline (approval timeout scheduler). |
| `api.lifecycle.drain_timeout_seconds` | 40.0 | Outer `asyncio.wait_for` budget around the cumulative stop sequence. |

**Rationale.** Audit-set placeholders. Rate-limiter GC at 1024
acquires balances bookkeeping latency against stale-bucket retention
under typical request volumes; the 60s horizon matches the common
sliding-window default. CAS retry of 2 (one retry) keeps mutation
contention bounded without amplifying load on a hot row. Dispatcher
3 attempts with 0.1s base / 1.0s cap absorbs a transient NATS
reconnect without pushing publish latency into the multi-second
tail. VRAM table (40GB->128, 16GB->64, 8GB->32) is a conventional
GPU-memory-to-batch heuristic for transformer fine-tunes; revisit
when distinct GPU profiles surface. Lifecycle stage budgets sum to
~33s under the 40s drain ceiling, leaving ~7s of headroom so the
outer hard deadline does not pre-empt normally-progressing stages
yet still bounds stuck-stage tail latency; persistence and
task-engine claim the largest slices
because they own connection-pool drains and in-flight task
finalisation respectively. Revisit when telemetry shows real-world
shutdown timing distributions.
