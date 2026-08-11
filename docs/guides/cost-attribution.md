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

The cost API lives under `/api/v1/budget`:

- `GET /api/v1/budget/config` returns the configured budget, thresholds, and currency.
- `GET /api/v1/budget/records` returns the raw record stream. It is paginated, and
  filterable by `agent_id`, `task_id`, `project_id`, `provider`, and a time window.
- `GET /api/v1/budget/agents/{agent_id}` returns one agent's total spend.
- `GET /api/v1/budget/prompt-class-breakdown` slices spend, latency, cache-hit, retry
  and success by prompt purpose.
- `GET /api/v1/analytics/overview` carries the period total, the remaining budget and
  the percentage used.

```bash
# The configured budget and its alert thresholds.
curl -s -b cookies.txt http://localhost:3001/api/v1/budget/config | jq

# Raw records for one agent (server default limit is 50).
curl -s -b cookies.txt "http://localhost:3001/api/v1/budget/records?agent_id=agent-1&limit=100" | jq

# Raw records for one project, since a date.
curl -s -b cookies.txt "http://localhost:3001/api/v1/budget/records?project_id=proj-acme&start=2026-05-01T00:00:00Z" | jq

# One agent's total.
curl -s -b cookies.txt http://localhost:3001/api/v1/budget/agents/agent-1 | jq
```

Every response that carries a money total carries what that total measures. Against a
connection billing by flat subscription the total is a correct zero that measures
nothing, so a percentage read on its own says the budget is untouched on exactly the
estate nobody can see. See [budget.md](../design/budget.md) for the verdicts.

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
        connection: ops-slack   # a bound SLACK connection holding the bot token
        channel: C0123456789
  min_severity: warning
```

The enforcer fires `BUDGET_PROJECT_BUDGET_EXCEEDED` and the dispatcher fans the notification out to every enabled sink at or above `min_severity`. On hard-stop (95% in the example), the project's tasks are auto-cancelled and a `notifications.budget_exhausted.send` event lands on the notification feed.

## Aggregation under concurrency

`CostTracker.record(...)` is async and lock-guarded; concurrent writes from many agents collapse to a single durable append. The per-currency invariant (`assert_currencies_match`) protects against accidental cross-currency rollups; mixed-currency calls raise at record time rather than silently producing a wrong total.

## Limitations

- `/api/v1/analytics/overview` reports the **billing period** total. Narrower windows
  come from `/api/v1/budget/records` with `start` / `end` bounds.
- Per-tool cost is NOT a first-class dimension. Tools are observed via `synthorg_tool_invocations_total`; cost attribution stops at the model + provider level.
- Project assignment relies on `task.project_id` being set; unassigned tasks aggregate under the implicit `unassigned` project bucket.
- Money attributes nothing on a flat-rate connection. The dimensions above still slice
  a zero four ways; bound those runs with `budget.run_hard_token_ceiling` instead.

## Observability

- `synthorg_cost_total` (gauge): total accumulated spend.
- `synthorg_budget_used_percent` (gauge): monthly utilisation.
- `synthorg_budget_spend_measurable` (gauge): whether the percentage beside it measures
  the period's spend (1 = every record metered, 0 = some or none). Read them together;
  a panel on the percentage alone shows an untouched budget on a flat-rate estate.
- `synthorg_budget_daily_used_percent` (gauge): daily utilisation (pro-rated).

Events emitted on every record:

- `budget.cost.recorded`: at successful persistence.
- `budget.cost.record_rejected`: at currency mismatch.
- `budget.enforcement.check`: pre-flight budget check (allow / downgrade / deny).

See [docs/design/budget.md](../design/budget.md) for the full design.
