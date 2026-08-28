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

Every top-level key is optional except `company_name`. Missing sections fall back to the Pydantic-defined defaults. Each agent declares a `department` by name, but the schema does not cross-check it against a declared `departments[].name` entry at load time.

`RootConfig` is `extra="forbid"`, so an unrecognised top-level key fails config load rather than being silently ignored. This page documents the sections an operator sets directly (above, plus `audit_chain` at root); the schema carries roughly forty further top-level blocks with sensible defaults, covering engine internals (routing, capability policy, coordination, stagnation/strategy detection, task engine, recovery, evolution, compaction), infrastructure (persistence, memory, api, sandboxing, mcp, queue, backup), and tool sub-configs (web, database, terminal, design, communication, analytics). Read `src/synthorg/config/schema.py::RootConfig` for the complete, current field list.

## `company_name` / `company_type`

| Field | Type | Default | Description |
|---|---|---|---|
| `company_name` | str | (required) | Company display name. |
| `company_type` | enum | `custom` | Company template type (e.g. `startup`, `agency`, `full_company`). |

Company-wide runtime settings (autonomy, default budget, communication pattern, tool access) live under the top-level `config:` block. The dashboard locale is not privileged towards any region: it resolves from the browser locale, falling back to the neutral `en` tag only when that is unavailable; see [docs/reference/regional-defaults.md](regional-defaults.md).

## `agents`

A list of agent definitions; each must declare at least `name`, `role`, and `department`. The agent `id` is derived deterministically from `name` and is not authored by hand.

Every LLM dispatch names an explicit `(provider, model)` pair; for an agent that pair lives inside `model` and nowhere else. `model` carries `provider` and `model_id` directly, there is no separate per-agent `provider` field and no default-provider auto-pick to fall back on. A `routing:` block still exists (strategy name, ordered routing rules, fallback chain) but does not substitute for a per-agent binding.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Agent display name (unique within the company). |
| `role` | str | (required) | Role name. |
| `department` | str | (required) | Name of the department this agent belongs to. |
| `model` | map | `{}` | Bound model config: `provider` and `model_id`, the explicit pair the agent dispatches through. |
| `memory` | map | `{}` | Raw memory config. |
| `tools` | map | `{}` | Raw tools config. |
| `authority` | map | `{}` | Raw authority config. |
| `autonomy_level` | enum | `null` | Per-agent autonomy override; `null` inherits the company default. |
| `strategic_output_mode` | enum | `null` | Per-agent strategic-output-mode override. |
| `model_requirement` | map | `null` | Raw model-requirement dict populated by the setup wizard; not typically hand-authored. |

## `departments`

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | (required) | Department name. |
| `head` | str | `null` | Department head role name (or agent identifier). |
| `head_id` | str | `null` | Optional unique identifier for the department head, disambiguating when several agents share `head`. Requires `head` to be set. |
| `budget_percent` | float (0..100) | `0` | Percentage of the company budget allocated to this department. |
| `teams` | list | `[]` | Teams within this department. |
| `reporting_lines` | list | `[]` | Subordinate-supervisor pairs. |
| `autonomy_level` | enum | `null` | Per-department autonomy override; `null` inherits the company default. |
| `policies` | map | (defaults) | Department-level operational policies: `review_requirements` and `approval_chains` (action-type-keyed, each action type unique). |

## `budget`

| Field | Type | Default | Description |
|---|---|---|---|
| `budget.total_monthly` | float | `100.0` | Monthly cap in `currency`. |
| `budget.currency` | str | `USD` | ISO 4217 code; relabels new cost records without converting past ones. |
| `budget.reset_day` | int (1..28) | `1` | Day-of-month for the monthly reset. |
| `budget.alerts.warn_at` | int (0..100) | `75` | Warning threshold percentage; must stay `warn_at < critical_at < hard_stop_at`. |
| `budget.alerts.critical_at` | int (0..100) | `90` | Critical threshold percentage. |
| `budget.alerts.hard_stop_at` | int (0..100) | `100` | Hard-stop threshold percentage. |
| `budget.per_task_limit` | float | `5.0` | Maximum cost per task. |
| `budget.per_agent_daily_limit` | float | `10.0` | Maximum cost per agent per day. |
| `budget.forecast_required` | bool | `true` | Require operator approval of a pre-flight cost forecast. |
| `budget.run_hard_ceiling` | float | `25.0` | Absolute real-money ceiling applied when a task carries no explicit hard ceiling; `0.0` opts out. |
| `budget.run_hard_token_ceiling` | int | `50000000` | Absolute token ceiling applied when a task carries no explicit hard token ceiling; `0` opts out. |
| `budget.risk_budget.enabled` | bool | `false` | Enable risk-weighted budget enforcement. |

This is the commonly-set subset; `BudgetConfig` (`src/synthorg/budget/config.py`) also carries per-model forecast priors, session token ceilings, per-provider `subscriptions` (quota tracking), and call-analytics knobs. See [docs/guides/budget.md](../guides/budget.md) for the broader operations guide.

## `integrations`

`integrations` is a fixed set of typed sub-blocks (the model is `extra="forbid"`):

```yaml
integrations:
  enabled: true
  connections:
    max_connections_per_type: 100
  webhooks:
    replay_window_seconds: 300
```

| Field | Type | Default | Description |
|---|---|---|---|
| `integrations.enabled` | bool | `true` | Master switch for the integrations layer. |
| `integrations.connections.max_connections_per_type` | int | `100` | Upper bound on stored connections per connection type. |
| `integrations.webhooks.rate_limit_rpm` | int | `100` | Max webhook requests per minute per connection. |
| `integrations.webhooks.replay_window_seconds` | int | `300` | Webhook nonce/timestamp dedup window. |
| `integrations.webhooks.max_payload_bytes` | int | `1000000` | Maximum webhook body size. |
| `integrations.webhooks.receipt_retention_days` | int | `0` | Days to keep webhook receipts; `0` never sweeps them. |
| `integrations.secret_backend` / `oauth` / `health` / `tunnel` / `mcp_catalog` | sub-block | (defaults) | Secret-storage, OAuth 2.1, health-monitoring, dev-tunnel, and bundled MCP catalog settings. |

There is no `integrations.webhooks.enabled` toggle: signature verification runs unconditionally on every delivery, so no switch exists to turn it off. Because the block is `extra="forbid"`, writing one is not merely ignored: it fails config validation.

Individual connections (GitHub, Slack, SMTP, database, generic HTTP, OAuth apps) are **not** declared in YAML: they are created at runtime through the integrations API and their secrets live in the configured secret backend.

## `notifications`

```yaml
notifications:
  sinks:
    - type: slack
      enabled: true
      params:
        connection: ops-slack   # a bound SLACK connection holding the bot token
        channel: C0123456789
    - type: console
      enabled: true
  min_severity: warning
```

| Field | Type | Default | Description |
|---|---|---|---|
| `notifications.sinks[].type` | enum | (required) | Adapter type: `console`, `ntfy`, `slack`, `email`. |
| `notifications.sinks[].enabled` | bool | `true` | Activate the sink. |
| `notifications.sinks[].params` | map[str,str] | `{}` | Adapter parameters (e.g. ntfy `topic`, Slack `connection` + `channel`). |
| `notifications.min_severity` | enum | `info` | Minimum severity to dispatch; one of `info`, `warning`, `error`. |

## `security`

| Field | Type | Default | Description |
|---|---|---|---|
| `security.enabled` | bool | `true` | Master switch for the security subsystem. |
| `security.enforcement_mode` | enum | `active` | `active`, `shadow`, or `disabled`. |
| `security.audit_enabled` | bool | `true` | Record audit entries. |
| `security.post_tool_scanning_enabled` | bool | `true` | Scan tool output for secrets. |
| `security.hard_deny_action_types` | list[str] | (preset) | Action types always denied. |
| `security.audit_retention_days` | int | `730` | Days to retain `audit_entries` before automatic purge (`0` disables). |

The hash-chained audit sink is a **root-level** block, not nested under `security`:

```yaml
audit_chain:
  enabled: false   # opt-in; signs every audit event into the hash chain
```

Prompt-safety wrapping (`wrap_untrusted`) is always applied in code and has no YAML toggle; approval-timeout policy is configured under `config.approval_timeout`, not `security`.

## `ontology`

User-defined entities live under `ontology.entities.entries`, not directly under `ontology`:

```yaml
ontology:
  entities:
    entries:
      - name: cost_centre
        definition: ...
        fields:
          owner: the team accountable for spend against this centre
        constraints: [...]
        disambiguation: ...
```

| Field | Type | Default | Description |
|---|---|---|---|
| `ontology.entities.entries[].name` | str | (required) | Entity name (unique within the list). |
| `ontology.entities.entries[].definition` | str | `""` | Free-text entity description. |
| `ontology.entities.entries[].fields` | map[str,str] | `{}` | Field name to description mapping. |
| `ontology.entities.entries[].constraints` | list[str] | `[]` | Business rule descriptions. |
| `ontology.entities.entries[].disambiguation` | str | `""` | Disambiguation text. |

`ontology` also carries `backend` (`"sqlite"`, the only current option) and sub-blocks for context injection, drift detection, delegation guarding, memory integration, and org-memory sync. See [docs/guides/ontology-extension.md](../guides/ontology-extension.md) for the entity-extension workflow.

## Validation

The loader applies the Pydantic schema, then runs validation hooks including uniqueness checks (agent names, department names), a queue/message-bus dependency (`queue.enabled` requires `backend == NATS` with a non-null `nats` sub-block, since the distributed queue publishes claims through the JetStream client), and routing-reference checks (a routing rule, fallback, or fallback-chain entry must name a model a provider actually declares).

Failures surface at startup with a typed `ConfigValidationError` and a line/column pointer into the YAML.

## Ingestion, not a live template

The YAML is not a precedence tier and there is no reload command for it: `load_config` runs once, at process boot, against the file named by `SYNTHORG_CONFIG_PATH` (default `company.yaml`). Its contents seed a `RootConfig` that flows into domain tables (departments, agents, budget) on `synthorg init`; from then on, the operator changes those entities through the dashboard and REST API against the persisted rows, not by re-applying or reloading the YAML file. Runtime-mutable values such as `budget.currency` are governed by the settings precedence chain in [configuration-precedence.md](configuration-precedence.md), independent of the YAML that first seeded them.
