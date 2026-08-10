---
title: Ceremony Scheduling Tuning
description: Configure ceremony cadence, protocol settings and budgets, swap scheduling strategies, observe ceremony firings.
---

# Ceremony Scheduling Tuning

Ceremonies are the meetings the platform runs during a sprint: planning, standups, reviews, retros. The `CeremonyScheduler` (`src/synthorg/engine/workflow/ceremony_scheduler.py`) owns the trigger state and delegates the "should this fire now?" decision to the active `CeremonySchedulingStrategy`. When a ceremony does fire, the `MeetingScheduler` runs it under the protocol the ceremony declares.

This guide covers cadence, protocol tuning, strategy selection, and what to look at when a ceremony does not fire.

## Concepts

- **Ceremony**: a named meeting with a protocol, a cadence, a token budget, and a participant list.
- **Cadence**: either a `frequency` (wall-clock) or a `policy_override.strategy_config.trigger` (a task milestone). A ceremony must declare at least one.
- **Strategy**: decides when each ceremony fires and whether the sprint auto-transitions. One strategy is active per sprint.

## Configuration surface

`SprintCeremonyConfig` (`src/synthorg/engine/workflow/sprint_config.py`) carries the per-ceremony tuning. Operators set it in the company template.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `ceremonies[].name` | str | (required) | Identifier, lowercase `[a-z0-9_-]`. Also the meeting type name. |
| `ceremonies[].protocol` | enum | (required) | `round_robin`, `structured_phases`, `position_papers`, `debate`, `silent_write`. |
| `ceremonies[].frequency` | enum | (unset) | `daily`, `weekly`, `bi_weekly`, `monthly`, `per_sprint_day`. |
| `ceremonies[].duration_tokens` | int | `5000` | Token budget for the meeting. |
| `ceremonies[].participants` | tuple[str] | `("all",)` | Department names, or `"all"`. |
| `ceremonies[].protocol_config` | object | protocol defaults | Protocol settings for this ceremony's meeting. |
| `ceremonies[].policy_override` | object | (unset) | Per-ceremony scheduling policy override. |
| `ceremony_policy.strategy` | enum | `task_driven` | Active strategy. |
| `ceremony_policy.auto_transition` | bool | `true` | Strategy may transition `ACTIVE` to `IN_REVIEW`. |
| `ceremony_policy.transition_threshold` | float | `1.0` | Completed-task fraction that triggers auto-transition. |
| `ceremony_policy.velocity_calculator` | enum | per strategy | Velocity calculator override. |

There is no `ceremonies[].type`, no `ceremonies[].trigger` block, and no `notification_target`. `SprintCeremonyConfig` and `CeremonyPolicyConfig` are both `extra="forbid"`, so a key that is not in the table above fails at load rather than being ignored.

## Cadence: frequency or trigger

A frequency-only ceremony fires on wall-clock cadence:

```yaml
workflow_config:
  sprint:
    ceremonies:
      - name: "daily_standup"
        protocol: "round_robin"
        frequency: "per_sprint_day"
        duration_tokens: 2000
```

A trigger-based ceremony fires at a task milestone, declared through `policy_override.strategy_config`:

```yaml
      - name: "retrospective"
        protocol: "position_papers"
        duration_tokens: 3000
        policy_override:
          strategy: "task_driven"
          strategy_config:
            trigger: "sprint_end"
```

The available triggers are `sprint_start`, `sprint_end`, `sprint_midpoint`, `every_n_completions` (with `every_n_completions: N`), and `sprint_percentage` (with `sprint_percentage: X`).

`task_driven`, `calendar` and `hybrid` all honour a bare `frequency`: `calendar` schedules on it, `hybrid` fires on whichever of cadence or milestone comes first, and `task_driven` uses it as the fallback for a ceremony that declares no trigger. This is what makes the four default ceremonies, which are frequency-only, fire under the default strategy.

## Tuning the meeting itself

`protocol_config` configures the meeting a ceremony runs. Its nested `protocol` is filled in from the ceremony's own `protocol`, so it is named once; supplying a different one is rejected at load.

```yaml
      - name: "sprint_planning"
        protocol: "structured_phases"
        frequency: "bi_weekly"
        protocol_config:
          structured_phases:
            conflict_detector: "embedding"
            max_discussion_tokens: 2000
```

Without this block a ceremony runs on protocol defaults, which is why `max_discussion_tokens` would otherwise stay at its flat 1000 regardless of the ceremony's own `duration_tokens`. See [Communication and Coordination](../design/communication-coordination.md) for each protocol's settings.

## Swap to a different strategy

Strategies live in `src/synthorg/engine/workflow/strategies/` and are selected by `ceremony_policy.strategy`:

| Strategy | Fires on |
|---|---|
| `task_driven` | Task-count milestones; frequency as fallback. |
| `calendar` | Wall-clock intervals from `frequency`. |
| `hybrid` | Calendar or task milestone, whichever is first. |
| `event_driven` | Engine events, with a debounce. |
| `budget_driven` | Cost-consumption thresholds. |
| `throughput_adaptive` | Throughput changing against a baseline. |
| `external_trigger` | External signals delivered over the webhook bridge. |
| `milestone_driven` | Semantic project milestones. |

Switching strategy emits `workflow.sprint.ceremony_strategy_changed`. A policy change takes effect at the next `activate_sprint`; there is no live-reload on the scheduler, so applying it to a running sprint means deactivating and re-activating:

```python
from synthorg.engine.state import EngineStateSlice

scheduler = app_state.slice(EngineStateSlice).ceremony_scheduler
await scheduler.deactivate_sprint()
# activate_sprint takes the sprint, its SprintConfig and the strategy;
# re-pass the same values so it re-reads the updated ceremony policy.
await scheduler.activate_sprint(sprint, config, strategy)
```

## Auto-transition

With `auto_transition: true` (the default), a strategy may transition the sprint from `ACTIVE` to `IN_REVIEW` once its condition is met: for `task_driven` that is the completed-task fraction reaching `transition_threshold`. The scheduler emits `workflow.sprint.auto_transition` before applying the status and `workflow.sprint.status_transitioned` after the write lands. The budget and milestone strategies emit `workflow.sprint.auto_transition_budget` and `workflow.sprint.auto_transition_milestone` respectively.

## Observability

Per-ceremony events:

- `workflow.sprint.ceremony_triggered`: the ceremony is about to run.
- `workflow.sprint.ceremony_skipped`: the strategy evaluated false this cycle, or the sprint is no longer active.
- `workflow.sprint.ceremony_trigger_failed`: dispatch raised, or no meeting ran, so the ceremony stays eligible.
- `workflow.sprint.ceremony_scheduler_started` / `_stopped`: sprint activation and deactivation.
- `workflow.sprint.ceremony_scheduler_start_failed`: activation failed and was rolled back.
- `workflow.sprint.ceremony_bridge_created`: a ceremony was bridged to its meeting type.
- `workflow.sprint.ceremony_policy_resolved` / `_policy_config_conflict`: policy inheritance outcomes.
- `workflow.sprint.strategy_config_invalid` / `ceremony_eval_context_invalid`: malformed strategy input.
- `workflow.sprint.ceremony_strategy_hook_failed` / `ceremony_deactivation_hook_failed`: a strategy lifecycle hook raised.
- `workflow.sprint.ceremony_notification_failed`: a strategy-migration notice could not be delivered.
- `workflow.sprint.ceremony_budget_snapshot_failed`: the budget read behind `budget_driven` failed.

Meeting-side events (`meeting.*`) cover what happens once a ceremony fires; `meeting.ceremony_types.registered` and `.cleared` mark the sprint installing and dropping its ceremony meeting types.

There are no Prometheus counters for ceremonies; the events above are the observability surface.

## Diagnostic checklist

| Symptom | Likely cause | Mitigation |
|---|---|---|
| A ceremony never fires | Its trigger threshold is above what the sprint reaches, or it declares neither `frequency` nor a trigger | Check `workflow.sprint.ceremony_skipped` for the reason field; lower the threshold or add a cadence. |
| Config loading fails on a ceremony | A key outside the table above (both models are `extra="forbid"`) | Remove the stray key; check the pydantic error for its name. |
| Load fails naming `protocol_config.protocol` | The nested protocol disagrees with the ceremony's | Drop the nested `protocol`; it is inherited. |
| A meeting runs on defaults despite tuning | The tuning is on a sub-config for a different protocol | Tune the block matching the ceremony's `protocol`. |
| Auto-transition does not fire | `auto_transition: false`, or the completed fraction is below `transition_threshold` | Set it true and check the threshold. |
| Starting a sprint returns 422 | Its ceremonies cannot be registered: a name collides with a configured meeting type, or two share a trigger | The error names the offending ceremony; rename it. |

See [Ceremony Scheduling](../design/ceremony-scheduling.md) for the full strategy catalogue and design rationale.
