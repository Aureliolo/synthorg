---
title: Ceremony Scheduling Tuning
description: Configure ceremony triggers and budgets, swap scheduling strategies, observe ceremony firings.
---

# Ceremony Scheduling Tuning

Ceremonies are scheduled meetings the platform fires during a sprint: standups, planning, retros, and one-off triggers. The `CeremonyScheduler` (`src/synthorg/engine/workflow/ceremony_scheduler.py`) owns trigger state and delegates the "should this fire now?" decision to the active `CeremonySchedulingStrategy`. This guide walks through tuning trigger thresholds, picking a strategy, and observing a ceremony round-trip.

## Concepts

- **Ceremony**: a scheduled meeting with a name, type, agenda, and a trigger.
- **Trigger**: a predicate that fires the ceremony (`task_completion_count`, `sprint_progress`, `budget_threshold`, `event_count`, `milestone`, `external_event`).
- **Strategy**: a plug-in that evaluates triggers and decides auto-transitions (`count_driven`, `event_driven`, `budget_driven`, `milestone_driven`, `throughput_adaptive`, `external_trigger`).

## Configuration surface

The `SprintCeremonyConfig` carries the per-ceremony tuning. Operators set it via the company template or runtime config service.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `ceremonies[].name` | str | (required) | Human-readable identifier. |
| `ceremonies[].trigger.kind` | enum | `count_driven` | Trigger family. |
| `ceremonies[].trigger.threshold` | int / float | (required) | Numeric gate. |
| `ceremony_policy.strategy` | enum | `count_driven` | Active strategy. |
| `ceremony_policy.auto_transition` | bool | `false` | Strategy may auto-transition the sprint when budget exhausted / milestone reached. |
| `ceremony_policy.notification_target` | str | (unset) | Operator notification channel on strategy migration. |

## Worked example: tune retro frequency

Default config fires the retro after every 20 task completions. Tighten it to fire after every 10 by setting:

```yaml
sprints:
  template:
    ceremonies:
      - name: retro
        type: retrospective
        trigger:
          kind: count_driven
          threshold: 10
    ceremony_policy:
      strategy: count_driven
      auto_transition: false
```

The next sprint's scheduler picks up the override at construction. To migrate a running sprint, call:

```python
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler

scheduler: CeremonyScheduler = app_state.ceremony_scheduler
await scheduler.reload_for_active_sprint()  # reads the updated config
```

## Swap to a different strategy

Strategies live in `src/synthorg/engine/workflow/ceremony_strategy.py` and are selected via the discriminator in `ceremony_policy.strategy`. Switching mid-sprint emits `workflow.sprint.ceremony_strategy_changed` with `from`, `to`, and the strategy-specific config delta.

Budget-driven example: fire planning when the sprint has burned 40% of its budget.

```yaml
ceremony_policy:
  strategy: budget_driven
ceremonies:
  - name: planning
    type: planning
    trigger:
      kind: budget_threshold
      threshold: 0.4
```

## Auto-transition

When `auto_transition: true`, the strategy may transition the sprint from `ACTIVE` to `RECONCILING` once its terminal condition is met (budget exhausted, milestone reached). The scheduler emits two events around the transition:

1. `workflow.sprint.auto_transition` BEFORE applying the new status.
2. `workflow.sprint.status_transitioned` AFTER the in-memory sprint object reflects the new status.

Both events carry `sprint_id`, `from_status`, and `to_status` for downstream dashboards.

## Observability

Per-ceremony events:

- `workflow.sprint.ceremony_triggered`: trigger fired, ceremony about to run.
- `workflow.sprint.ceremony_skipped`: strategy evaluated false; ceremony not fired this cycle.
- `workflow.sprint.ceremony_trigger_failed`: dispatching the meeting raised (event swallowed).
- `workflow.sprint.event_counter_incremented`: per-event counters for event-driven strategies.

Counters and gauges (with bounded labels):

- `synthorg_ceremony_triggered_total` counter, `ceremony` (registry-bound), `strategy`.
- `synthorg_ceremony_skipped_total` counter, same labels.

## Diagnostic checklist

| Symptom | Likely cause | Mitigation |
|---|---|---|
| Retro never fires | `count_driven` threshold too high vs. observed completions | Lower threshold or switch strategy to `event_driven`. |
| Strategy migration loops | Two strategies enabled simultaneously by mistake | Confirm `ceremony_policy.strategy` is exactly one value; remove stray YAML keys. |
| Auto-transition does not fire | `auto_transition: false` or terminal condition unmet | Set true and verify the trigger threshold via the dashboard. |
| Notification missing on strategy change | `notification_target` unset | Set the notification target or check the dispatcher's logs for `SPRINT_CEREMONY_NOTIFICATION_FAILED`. |

See [docs/design/ceremony-scheduling.md](../design/ceremony-scheduling.md) for the full strategy catalogue and design rationale.
