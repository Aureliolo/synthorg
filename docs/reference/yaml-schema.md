---
title: YAML Schema Reference
description: Field-by-field reference for the SynthOrg company-template YAML format.
---

# YAML Schema Reference

The company template YAML describes a synthetic organisation: its agents, departments, budget, integrations, sprints, and operational policies. The schema is enforced by Pydantic models under `src/synthorg/config/schema.py`; this document captures the field set as a fixed-state reference. For the broader configuration precedence story (DB > env > code default) see [docs/reference/configuration-precedence.md](configuration-precedence.md).

## Top-level shape

```yaml
company_name: Acme Robotics
company_type: startup
agents:
  - name: ...
departments:
  - name: ...
sprints:
  template: ...
budget:
  ...
integrations:
  ...
notifications:
  ...
security:
  ...
meta_loop:
  ...
ontology:
  ...
scoring:
  ...
```

Every top-level key is optional except `company_name`. Missing sections fall back to the Pydantic-defined defaults.

## `company_name` / `company_type`

| Field | Type | Default | Description |
|---|---|---|---|
| `company_name` | str | (required) | Company display name. |
| `company_type` | enum | `custom` | Company template type (e.g. `startup`, `agency`, `full_company`). |

Company-wide runtime settings (autonomy, default budget, communication pattern, tool access) live under the top-level `config:` block. The dashboard locale defaults to `en-GB` per the regional-defaults rule; see [docs/reference/regional-defaults.md](regional-defaults.md).

## `agents`

A list of agent definitions; each must declare at least `name`, `role`, and `provider`. The agent `id` is derived deterministically from `name` and is not authored by hand.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Agent display name (unique within the company). |
| `role` | str | (required) | Role name; matches a department's `roles` list. |
| `provider` | str | (required) | Provider driver identifier. |
| `model` | str | provider default | Model identifier (e.g. `example-medium-001`). |
| `system_prompt` | str | derived | Override the role-default system prompt. |
| `tools` | list[str] | `[]` | Tool names this agent may invoke. |
| `trust_level` | enum | `standard` | One of `restricted`, `standard`, `elevated`. |
| `cost_centre` | str | `unassigned` | Cost-attribution bucket. |

## `departments`

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Department name. |
| `roles` | list[str] | (required) | Roles available in this department. |
| `head` | str | (optional) | `id` of the head agent. |

## `sprints`

| Field | Type | Default | Description |
|---|---|---|---|
| `sprints.template.duration_days` | int | `14` | Default sprint length. |
| `sprints.template.start_day` | str | `monday` | Day of week to start. |
| `sprints.template.ceremonies` | list | `[]` | Ceremony definitions; see [ceremony-scheduling-tuning](../guides/ceremony-scheduling-tuning.md). |
| `sprints.template.ceremony_policy.strategy` | enum | `count_driven` | Scheduling strategy. |
| `sprints.template.ceremony_policy.auto_transition` | bool | `false` | Strategy may auto-transition the sprint. |

## `budget`

| Field | Type | Default | Description |
|---|---|---|---|
| `budget.total_monthly` | float | `0` | Monthly cap in `currency`. |
| `budget.currency` | str | `GBP` | ISO 4217 code. |
| `budget.reset_day` | int (1..28) | `1` | Day-of-month for the monthly reset. |
| `budget.alerts.warn_at` | int (0..100) | `75` | Warning threshold percentage. |
| `budget.alerts.critical_at` | int (0..100) | `90` | Critical threshold percentage. |
| `budget.alerts.hard_stop_at` | int (0..100) | `100` | Hard-stop threshold percentage. |
| `budget.projects.<id>.monthly` | float | unset | Per-project monthly cap. |
| `budget.risk_budget.enabled` | bool | `false` | Enable risk-weighted budget enforcement. |

See [docs/guides/budget.md](../guides/budget.md) for the broader operations guide.

## `integrations`

Each integration is a typed sub-block keyed by name:

```yaml
integrations:
  github:
    enabled: true
    token: ${GITHUB_TOKEN}
  slack:
    enabled: true
    webhook_url: ${SLACK_WEBHOOK}
  webhooks:
    enabled: true
    replay_window_seconds: 300
```

| Field | Type | Default | Description |
|---|---|---|---|
| `integrations.<name>.enabled` | bool | `false` | Activate the integration. |
| `integrations.<name>.token` / `webhook_url` / `api_key` | str | (provider) | Secret material; `${ENV_VAR}` substitution supported. |
| `integrations.webhooks.replay_window_seconds` | int | `300` | Webhook replay protection. |

## `notifications`

```yaml
notifications:
  sinks:
    - type: slack
      enabled: true
      params:
        webhook_url: ${SLACK_WEBHOOK}
    - type: console
      enabled: true
  min_severity: warning
```

| Field | Type | Default | Description |
|---|---|---|---|
| `notifications.sinks[].type` | enum | (required) | Adapter type: `console`, `ntfy`, `slack`, `email`. |
| `notifications.sinks[].enabled` | bool | `true` | Activate the sink. |
| `notifications.sinks[].params` | map[str,str] | `{}` | Adapter parameters (e.g. `webhook_url`). |
| `notifications.min_severity` | enum | `info` | Minimum severity to dispatch; one of `info`, `warning`, `error`. |

## `security`

| Field | Type | Default | Description |
|---|---|---|---|
| `security.audit_chain.enabled` | bool | `true` | Sign every audit event into the hash chain. |
| `security.audit_chain.max_entries` | int | `100000` | Ring-buffer capacity; alert at 90%. |
| `security.approval.timeout_seconds` | int | `86400` | Auto-reject pending approvals after this window. |
| `security.approval.reviewer_groups` | list[str] | `[]` | Roles allowed to decide. |
| `security.prompt_safety.enabled` | bool | `true` | Wrap untrusted content via `wrap_untrusted`. |

## `meta_loop`

| Field | Type | Default | Description |
|---|---|---|---|
| `meta_loop.enabled` | bool | `false` | Activate the meta-loop. |
| `meta_loop.rules` | list | `[]` | Rule activations (see [custom-rules-and-meta-loop](../guides/custom-rules-and-meta-loop.md)). |
| `meta_loop.rules[].name` | str | (required) | Rule identifier. |
| `meta_loop.rules[].severity` | enum | `warn` | One of `info`, `warn`, `fail`. |

## `ontology`

```yaml
ontology:
  terms:
    - name: cost_centre
      description: ...
      examples: [...]
```

See [docs/guides/ontology-extension.md](../guides/ontology-extension.md) for the term-extension workflow.

## `scoring`

| Field | Type | Default | Description |
|---|---|---|---|
| `scoring.strategy` | str | `composite` | Active scoring strategy. |
| `scoring.<hyperparam>` | float / int | per strategy | Strategy-specific tuning (e.g. `freshness_boost_factor`). |

## Validation

The loader applies the Pydantic schema, then runs validation hooks:

- Currency consistency (`budget.currency` must match per-project currencies).
- Cross-section references (`agents[].role` must appear in some `departments[].roles`).
- Allowlist enforcement (`notifications.sinks[].type` must be a supported sink adapter).

Failures surface at startup with a typed `ConfigValidationError` and a line/column pointer into the YAML.

## Reload semantics

Most fields are hot-reloadable via `synthorg config reload-template`. Exceptions:

- `version` change requires a process restart.
- New agents require a worker pool restart (the runtime caches per-agent prompts).
- `budget.currency` change requires draining the cost tracker first; the runtime refuses the reload until the tracker is empty.
