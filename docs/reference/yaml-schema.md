---
title: YAML Schema Reference
description: Field-by-field reference for the SynthOrg company-template YAML format.
---

# YAML Schema Reference

The company template YAML describes a synthetic organisation: its agents, departments, budget, integrations, and operational policies. The schema is enforced by Pydantic models under `src/synthorg/config/schema.py`; this document captures the field set as a fixed-state reference. For the broader configuration precedence story (DB > env > code default) see [docs/reference/configuration-precedence.md](configuration-precedence.md).

## Top-level shape

```yaml
company_name: Acme Robotics
company_type: startup
agents:
  - name: ...
departments:
  - name: ...
budget:
  ...
integrations:
  ...
notifications:
  ...
security:
  ...
ontology:
  ...
```

Every top-level key is optional except `company_name`. Missing sections fall back to the Pydantic-defined defaults. One cross-section dependency applies: defining `agents` makes `departments` required, since every agent's `department` is mandatory and must name a declared department (see [Validation](#validation)).

## `company_name` / `company_type`

| Field | Type | Default | Description |
|---|---|---|---|
| `company_name` | str | (required) | Company display name. |
| `company_type` | enum | `custom` | Company template type (e.g. `startup`, `agency`, `full_company`). |

Company-wide runtime settings (autonomy, default budget, communication pattern, tool access) live under the top-level `config:` block. The dashboard locale defaults to `en-GB` per the regional-defaults rule; see [docs/reference/regional-defaults.md](regional-defaults.md).

## `agents`

A list of agent definitions; each must declare at least `name`, `role`, and `department`. The agent `id` is derived deterministically from `name` and is not authored by hand. Provider and model selection flow through the routing layer (`routing:` plus per-agent `model:` hints), not a per-agent `provider` field.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Agent display name (unique within the company). |
| `role` | str | (required) | Role name. |
| `department` | str | (required) | Name of the department this agent belongs to. |
| `level` | enum | `mid` | Seniority level (`junior`..`c_suite`). |
| `personality_preset` | str | `null` | Named personality preset; round-trips from template setup. |
| `personality` | map | `{}` | Raw personality config (Big Five dimensions, decision-making style). |
| `model` | map | `{}` | Raw model-selection hints (`tier`, `priority`, `min_context`). |
| `memory` | map | `{}` | Raw memory config. |
| `tools` | map | `{}` | Raw tools config. |
| `authority` | map | `{}` | Raw authority config. |
| `autonomy_level` | enum | `null` | Per-agent autonomy override; `null` inherits the company default. |
| `strategic_output_mode` | enum | `null` | Per-agent strategic-output-mode override. |

## `departments`

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Department name. |
| `head` | str | `null` | Role name (or agent identifier) of the department head. |
| `head_id` | str | `null` | Unique identifier disambiguating the head when several agents share `head`. |
| `budget_percent` | int | `0` | Percentage of the company budget allocated to this department. |
| `teams` | list | `[]` | Teams within this department. |
| `reporting_lines` | list | `[]` | Subordinate-supervisor pairs. |

## `budget`

| Field | Type | Default | Description |
|---|---|---|---|
| `budget.total_monthly` | float | `0` | Monthly cap in `currency`. |
| `budget.currency` | str | `USD` | ISO 4217 code. |
| `budget.reset_day` | int (1..28) | `1` | Day-of-month for the monthly reset. |
| `budget.alerts.warn_at` | int (0..100) | `75` | Warning threshold percentage. |
| `budget.alerts.critical_at` | int (0..100) | `90` | Critical threshold percentage. |
| `budget.alerts.hard_stop_at` | int (0..100) | `100` | Hard-stop threshold percentage. |
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

## `ontology`

```yaml
ontology:
  terms:
    - name: cost_centre
      description: ...
      examples: [...]
```

See [docs/guides/ontology-extension.md](../guides/ontology-extension.md) for the term-extension workflow.

## Validation

The loader applies the Pydantic schema, then runs validation hooks:

- Cross-section references (`agents[].department` must name a declared `departments[].name`).
- Allowlist enforcement (`notifications.sinks[].type` must be a supported sink adapter).

Failures surface at startup with a typed `ConfigValidationError` and a line/column pointer into the YAML.

## Reload semantics

Most fields are hot-reloadable via `synthorg config reload-template`. Exceptions:

- `version` change requires a process restart.
- New agents require a worker pool restart (the runtime caches per-agent prompts).
- `budget.currency` change requires draining the cost tracker first; the runtime refuses the reload until the tracker is empty.
