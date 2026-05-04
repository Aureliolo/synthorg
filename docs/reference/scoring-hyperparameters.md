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
| `engine.quality.heuristic.pass_threshold` | 0.5 | Probe-pass-ratio cutoff for the PASS verdict. |
| `engine.quality.heuristic.pass_grade` | 0.8 | Per-criterion grade on pass. |
| `engine.quality.heuristic.fail_grade` | 0.3 | Per-criterion grade on fail. |
| `engine.quality.heuristic.confidence_ceiling` | 0.9 | Maximum reported confidence. |
| `engine.quality.heuristic.confidence_bias` | 0.1 | Additive bias on derived confidence (prevents 0%). |

**Rationale.** Audit-set placeholders. Pass threshold of 0.5 means
"more than half the probes match". Pass/fail grades of 0.8/0.3 give a
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

## Pending migrations

The following audit-cited sites in clusters #28 and #29 are tracked
in the AST gate's baseline but not yet migrated to settings (single-PR
scope cap). Follow-up issues filed against #1739 cover each:

- Rate-limiter GC threshold and horizon (`api/rate_limits/in_memory*.py`).
- CAS retry attempts (`core/concurrency/cas_retry.py`).
- Bus bridge max errors + drain timeout fallback constants
  (`api/bus_bridge.py`; already partially settings-driven, only the
  fallback module constants remain bare).
- Task dispatcher publish retry attempts and backoff bounds
  (`workers/dispatcher.py`).
- VRAM-to-batch-size table (`api/controllers/memory.py`).
- Fine-tune text chunk size (`memory/fine_tune.py`).
- Loop-prevention rate-limit window fallback constant
  (`communication/loop_prevention/rate_limit.py`; already partially
  settings-driven).
- Lifecycle shutdown stage budgets (`api/lifecycle.py`, 11 sites).

Each follow-up retains the settings-as-source-of-truth pattern
established in this PR: register the setting in
`src/synthorg/settings/definitions/<namespace>.py`, add the field to
the matching `*BridgeConfig`, wire the resolver entry, and refactor
the consumer to read the resolved value.
