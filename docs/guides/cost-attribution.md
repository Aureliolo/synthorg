---
title: Cost Attribution
description: Slice spend by provider, model, agent, and project; query the rollup; route alerts at the right granularity.
---

# Cost Attribution

SynthOrg records every LLM call with a `CostRecord` (`src/synthorg/budget/cost_record.py`) that carries enough dimensions to slice spend four ways: provider, model, agent, and project. This guide walks through reading the rollup, choosing the right query, and wiring alerts at each granularity.

## Dimensions

| Dimension | Source | Cardinality |
|---|---|---|
| Provider | Provider driver name | ~10 |
| Model | Provider model identifier | ~50 |
| Agent | `agent_id` from the executing context | Registry-bound (~100s) |
| Project | `project_id` from the task context | Hundreds to thousands |

All dimensions are bounded label values when surfaced as Prometheus metrics; see [docs/guides/monitoring.md](monitoring.md) for the registry-bound enforcement rule.

## Querying the rollup

The cost API lives at `/api/v1/costs`. Three core endpoints:

- `GET /api/v1/costs/summary` returns the current period totals across dimensions.
- `GET /api/v1/costs/by/{dim}` returns rollups for a single dimension (e.g. `by/agent`).
- `GET /api/v1/costs/records` returns the raw record stream (paginated).

```bash
# Summary for the current billing month.
curl -s -b cookies.txt http://localhost:3001/api/v1/costs/summary | jq

# Per-agent breakdown.
curl -s -b cookies.txt "http://localhost:3001/api/v1/costs/by/agent?since=2026-05-01" | jq

# Per-project breakdown filtered to one project.
curl -s -b cookies.txt "http://localhost:3001/api/v1/costs/by/project?project_id=proj-acme" | jq
```

## Worked example: route a Slack alert at 80% project budget

Set the project budget in the company template:

```yaml
budget:
  projects:
    proj-acme:
      monthly: 250.00
      currency: GBP
      alerts:
        warn_at: 50
        critical_at: 80
        hard_stop_at: 95
```

Configure the notification dispatcher to route warning-and-above alerts to Slack:

```yaml
notifications:
  sinks:
    - type: slack
      enabled: true
      params:
        webhook_url: https://hooks.slack.com/services/.../...
  min_severity: warning
```

The enforcer fires `BUDGET_PROJECT_BUDGET_EXCEEDED` and the dispatcher fans the notification out to every enabled sink at or above `min_severity`. On hard-stop (95% in the example), the project's tasks are auto-cancelled and a `notifications.budget_exhausted.send` event lands on the notification feed.

## Aggregation under concurrency

`CostTracker.record(...)` is async and lock-guarded; concurrent writes from many agents collapse to a single durable append. The per-currency invariant (`assert_currencies_match`) protects against accidental cross-currency rollups; mixed-currency calls raise at record time rather than silently producing a wrong total.

## Limitations

- The summary endpoint reports the **billing period** total. Daily totals come from `/api/v1/costs/by/agent?period=day` with the appropriate filter.
- Per-tool cost is NOT a first-class dimension. Tools are observed via `synthorg_tool_invocations_total`; cost attribution stops at the model + provider level.
- Project assignment relies on `task.project_id` being set; unassigned tasks aggregate under the implicit `unassigned` project bucket.

## Observability

- `synthorg_cost_total` (gauge): total accumulated spend.
- `synthorg_agent_cost_total` (gauge, `agent_id` registry-bound): per-agent cumulative spend.
- `synthorg_budget_used_percent` (gauge): monthly utilisation.
- `synthorg_budget_daily_used_percent` (gauge): daily utilisation (pro-rated).

Events emitted on every record:

- `budget.cost.recorded`: at successful persistence.
- `budget.cost.record_rejected`: at currency mismatch.
- `budget.enforcement.check`: pre-flight budget check (allow / downgrade / deny).

See [docs/design/budget.md](../design/budget.md) for the full design.
