---
title: Company Configuration
description: Complete YAML reference for all SynthOrg configuration options.
---

# Company Configuration

SynthOrg organizations are configured via YAML. The configuration defines your company structure, agents, LLM providers, budget, security policies, memory, tools, and operational settings. All configuration is validated at load time using Pydantic; invalid values produce clear error messages with field paths.

---

## Overview

Configuration is loaded from a YAML file and validated into a frozen Pydantic model (`RootConfig`). Once loaded, configuration is immutable; runtime state changes (e.g. agent execution status) use separate mutable models.

The configuration can be provided:

- Via the **setup wizard** in the web dashboard (recommended for first-time setup)
- As a **YAML file** passed to the CLI or API
- Via **templates** that pre-populate agents, departments, and workflows

---

## Root Configuration

The top-level configuration object. Only `company_name` is required; all other fields have sensible defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `company_name` | string | *(required)* | Company identifier |
| `company_type` | CompanyType | `custom` | Company template type |
| `departments` | list | `[]` | Organizational departments |
| `agents` | list | `[]` | Agent configurations |
| `custom_roles` | list | `[]` | User-defined role catalog |
| `config` | CompanyConfig | *(defaults)* | Company-wide settings |
| `budget` | BudgetConfig | *(defaults)* | Budget and cost controls |
| `communication` | CommunicationConfig | *(defaults)* | Messaging architecture |
| `providers` | dict | `{}` | LLM provider configurations |
| `routing` | RoutingConfig | *(defaults)* | Model routing strategy |
| `logging` | LogConfig | `null` | Observability configuration |
| `graceful_shutdown` | GracefulShutdownConfig | *(defaults)* | Process lifecycle |
| `workflow_handoffs` | list | `[]` | Cross-department handoffs |
| `escalation_paths` | list | `[]` | Cross-department escalation |
| `task_assignment` | TaskAssignmentConfig | *(defaults)* | Task assignment strategy |
| `memory` | CompanyMemoryConfig | *(defaults)* | Memory backend settings |
| `persistence` | PersistenceConfig | *(defaults)* | Database backend settings |
| `cost_tiers` | CostTiersConfig | *(defaults)* | Model pricing tiers |
| `org_memory` | OrgMemoryConfig | *(defaults)* | Shared organizational memory |
| `api` | ApiConfig | *(defaults)* | API server settings |
| `sandboxing` | SandboxingConfig | *(defaults)* | Code execution sandbox |
| `mcp` | MCPConfig | *(defaults)* | MCP tool bridge |
| `security` | SecurityConfig | *(defaults)* | Security rule engine |
| `trust` | TrustConfig | *(defaults)* | Progressive trust |
| `promotion` | PromotionConfig | *(defaults)* | Career progression |
| `task_engine` | TaskEngineConfig | *(defaults)* | Task engine settings |
| `coordination` | CoordinationConfig | *(defaults)* | Multi-agent coordination |
| `git_clone` | GitCloneConfig | *(defaults)* | Git workspace settings |
| `backup` | BackupConfig | *(defaults)* | Backup/restore settings |
| `coordination_metrics` | CoordinationMetricsConfig | *(defaults)* | Coordination metrics tracking |

---

## Company Settings

### Company Types

The `company_type` field selects a pre-defined organizational template:

| Type | Description | Agents |
|------|-------------|--------|
| `solo_founder` | Solo builder with full autonomy | 2 |
| `startup` | CEO + small engineering team | 3--5 |
| `dev_shop` | Engineering squad with quality gates | 6--10 |
| `product_team` | Product-focused studio with design | 8--12 |
| `agency` | Project manager + specialists | 4--8 |
| `full_company` | Enterprise org with all departments | 8--15 |
| `research_lab` | Lead researcher + assistants | 5--10 |
| `consultancy` | Client-facing advisory and delivery | 4--6 |
| `data_team` | Analytics and ML-focused team | 5--8 |
| `custom` | Build from scratch | Any |

### Autonomy Levels

Set the company-wide autonomy level in `config.autonomy.level`:

| Level | Behavior |
|-------|----------|
| `full` | Agents execute all actions without approval |
| `semi` | Risky actions require human approval |
| `supervised` | Most actions require human approval |
| `locked` | All actions require human approval |

Individual agents can override the company-wide autonomy level via their `autonomy_level` field.

---

## Providers

LLM providers are configured under the `providers` key. Each entry is a named provider with connection details, models, and resilience settings.

### Provider Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `driver` | string | `"litellm"` | Backend driver |
| `litellm_provider` | string | `null` | LiteLLM routing key override |
| `family` | string | `null` | Provider family for grouping |
| `auth_type` | AuthType | `"api_key"` | Authentication method |
| `api_key` | string | `null` | API key (required for `api_key` auth) |
| `subscription_token` | string | `null` | Subscription token |
| `base_url` | string | `null` | Custom API base URL |
| `models` | list | `[]` | Available models |
| `retry` | RetryConfig | *(defaults)* | Retry settings for transient errors |
| `rate_limiter` | RateLimiterConfig | *(defaults)* | Client-side rate limiting |
| `subscription` | SubscriptionConfig | `null` | Quota and subscription tracking |
| `degradation` | DegradationConfig | `null` | Quota exhaustion strategy |

### Authentication Types

| Auth Type | Required Fields |
|-----------|-----------------|
| `api_key` | `api_key` |
| `subscription` | `subscription_token`, `tos_accepted_at` |
| `oauth` | OAuth-specific fields |
| `custom_header` | `custom_header_name`, `custom_header_value` |
| `none` | No credentials (e.g. local Ollama) |

### Model Configuration

Each provider lists its available models under the `models` key:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | *(required)* | Model identifier |
| `alias` | string | `null` | Short alias for routing rules |
| `cost_per_1k_input` | float | `0.0` | Cost per 1K input tokens (USD) |
| `cost_per_1k_output` | float | `0.0` | Cost per 1K output tokens (USD) |
| `max_context` | int | `200000` | Context window size in tokens |
| `estimated_latency_ms` | int | `null` | Estimated latency |

### Provider Examples

=== "Cloud Provider"

    ```yaml
    providers:
      my-cloud:
        auth_type: api_key
        api_key: "sk-..."
        models:
          - id: "example-large-001"
            alias: "large"
            cost_per_1k_input: 0.015
            cost_per_1k_output: 0.075
            max_context: 200000
          - id: "example-small-001"
            alias: "small"
            cost_per_1k_input: 0.001
            cost_per_1k_output: 0.005
    ```

=== "Ollama (Local)"

    ```yaml
    providers:
      local-ollama:
        auth_type: none
        base_url: "http://host.docker.internal:11434"
        models:
          - id: "llama3:8b"
            alias: "medium"
            max_context: 8192
    ```

=== "Subscription Provider"

    ```yaml
    providers:
      my-subscription:
        auth_type: subscription
        subscription_token: "sub-token-..."
        tos_accepted_at: "2026-01-15T00:00:00Z"
        base_url: "https://api.example.com/v1"
        subscription:
          monthly_quota: 1000000
        models:
          - id: "example-medium-001"
            alias: "medium"
    ```

---

## Model Routing

The `routing` section controls how models are selected for agent tasks.

### Routing Strategies

| Strategy | Description |
|----------|-------------|
| `manual` | Explicit model assignment per agent |
| `role_based` | Match models to agent seniority |
| `cost_aware` | Balance quality vs. cost |
| `cheapest` | Always use the cheapest model |
| `fastest` | Always use the fastest model |
| `smart` | Cascade: override > task-type > role > seniority > cheapest > fallback |

### Routing Rules

Rules are evaluated in order. Each rule matches by `role_level` and/or `task_type`:

```yaml
routing:
  strategy: cost_aware
  rules:
    - role_level: c_suite
      preferred_model: "large"
    - role_level: senior
      preferred_model: "medium"
      fallback: "small"
    - task_type: review
      preferred_model: "small"
  fallback_chain:
    - "medium"
    - "small"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `role_level` | SeniorityLevel | `null` | Seniority filter |
| `task_type` | string | `null` | Task type filter |
| `preferred_model` | string | *(required)* | Preferred model alias or ID |
| `fallback` | string | `null` | Fallback model |

!!! note

    At least one of `role_level` or `task_type` must be set per rule.

---

## Agents

Agent configuration is covered in detail in the [Agent Roles & Hierarchy](agents.md) guide. Key fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | *(required)* | Agent display name |
| `role` | string | *(required)* | Role identifier |
| `department` | string | *(required)* | Department name |
| `level` | SeniorityLevel | `mid` | Seniority level |
| `personality` | dict | `{}` | Personality configuration |
| `model` | dict | `{}` | Model assignment (tier, priority, min_context) |
| `memory` | dict | `{}` | Per-agent memory settings |
| `tools` | dict | `{}` | Tool access configuration |
| `authority` | dict | `{}` | Delegation and approval authority |
| `autonomy_level` | AutonomyLevel | `null` | Per-agent autonomy override |

Agent names must be unique within the organization.

---

## Budget

Budget configuration is covered in detail in the [Budget & Cost Control](budget.md) guide. Key fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `total_monthly` | float | `100.0` | Monthly budget limit |
| `currency` | string | `"USD"` | ISO 4217 currency code (display-only; no FX conversion) |
| `per_task_limit` | float | `5.0` | Maximum cost per task |
| `per_agent_daily_limit` | float | `10.0` | Maximum cost per agent per day |
| `reset_day` | int | `1` | Budget reset day (1--28) |
| `alerts` | BudgetAlertConfig | *(defaults)* | Alert thresholds |
| `auto_downgrade` | AutoDowngradeConfig | *(defaults)* | Auto-downgrade settings |

---

## Memory

Memory configuration is covered in detail in the [Memory Configuration](memory.md) guide. Key fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | string | `"mem0"` | Memory backend |
| `level` | MemoryLevel | `"session"` | Default persistence level |
| `storage` | MemoryStorageConfig | *(defaults)* | Storage paths |
| `options` | MemoryOptionsConfig | *(defaults)* | Behavior options |
| `retrieval` | MemoryRetrievalConfig | *(defaults)* | Retrieval pipeline |
| `consolidation` | ConsolidationConfig | *(defaults)* | Consolidation settings |

---

## Persistence

Operational data persistence (tasks, cost records, messages, workflows, audit entries, etc.) has two supported backends:

- **SQLite** (default): zero-ops file database, great for single-process deployments
- **Postgres**: production backend with native JSONB, TIMESTAMPTZ, connection pooling; required for multi-instance deployments

```yaml
persistence:
  backend: "postgres"              # "sqlite" (default) or "postgres"
  sqlite:
    path: "/data/synthorg.db"    # file path; used when backend == "sqlite"
  postgres:                        # used when backend == "postgres"
    host: "db.internal"
    port: 5432
    database: "synthorg"
    username: "synthorg_app"
    password: "${POSTGRES_PASSWORD}"  # SecretStr -- redacted from logs
    ssl_mode: "verify-full"          # prefer verify-full in production
    pool_min_size: 1
    pool_max_size: 10
    pool_timeout_seconds: 30.0
    application_name: "synthorg"
    statement_timeout_ms: 30000
    connect_timeout_seconds: 10.0
```

The Postgres backend requires the optional extra: install with
`uv pip install 'synthorg[postgres]'` (or `pip install 'synthorg[postgres]'`).
Without the extra, selecting `backend: postgres` raises an actionable
`PersistenceConnectionError` at startup.

Full field reference and architectural rationale are in the
[Memory / Persistence](../design/memory.md) design page.

---

## Security & Trust

Security configuration is covered in detail in the [Security & Trust Policies](security.md) guide. Key fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `security.enabled` | bool | `true` | Master security switch |
| `security.audit_enabled` | bool | `true` | Audit logging |
| `security.output_scan_policy_type` | string | `"autonomy_tiered"` | Output scan policy |
| `trust.strategy` | string | `"disabled"` | Trust strategy type |
| `trust.initial_level` | ToolAccessLevel | `"standard"` | Default trust level |

---

## MCP (Tool Bridge)

MCP configuration is covered in detail in the [Tool Integration (MCP)](mcp-tools.md) guide. Key fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mcp.servers` | list | `[]` | MCP server configurations |

---

## Operational Settings

### Task Assignment

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `strategy` | string | `"role_based"` | Assignment strategy |
| `min_score` | float | `0.1` | Minimum capability score (0.0--1.0) |
| `max_concurrent_tasks_per_agent` | int | `5` | Concurrency limit (1--50) |

Valid strategies: `manual`, `role_based`, `load_balanced`, `cost_optimized`, `hierarchical`, `auction`.

### Graceful Shutdown

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `strategy` | string | `"cooperative_timeout"` | Shutdown strategy |
| `grace_seconds` | float | `30.0` | Grace period for cooperative exit (0--300] |
| `cleanup_seconds` | float | `5.0` | Cleanup time (0--60] |

### Departments

```yaml
departments:
  - name: "engineering"
    budget_percent: 60
    head_role: "CTO"
    reporting_lines:
      - subordinate: "Full-Stack Developer"
        supervisor: "CTO"
  - name: "product"
    budget_percent: 40
    head_role: "Product Manager"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | *(required)* | Department name (must be unique) |
| `budget_percent` | int | `0` | Percentage of company budget |
| `head_role` | string | *(required)* | Department head role |
| `reporting_lines` | list | `[]` | Subordinate-supervisor relationships |

### Workflow Handoffs

Define cross-department handoff triggers:

```yaml
workflow_handoffs:
  - from_department: "engineering"
    to_department: "product"
    trigger: "Feature implementation completed for product review"
    artifacts:
      - "pull_request"
      - "release_notes"
```

### Escalation Paths

Define cross-department escalation routes:

```yaml
escalation_paths:
  - from_department: "engineering"
    to_department: "executive"
    condition: "Technical blocker requiring executive decision"
    priority_boost: 1
```

---

## Validation Rules

SynthOrg enforces the following cross-field validation rules at load time:

| Rule | Description |
|------|-------------|
| Unique agent names | Agent names must be unique across the organization |
| Unique department names | Department names must not repeat |
| Routing model references | `preferred_model` and `fallback` in routing rules must reference existing model IDs or aliases |
| Fallback chain references | `fallback_chain` entries must reference existing model IDs or aliases |
| Alert threshold ordering | `warn_at < critical_at < hard_stop_at` (budget alerts) |
| No self-downgrade | `downgrade_map` entries must not map a model to itself |
| Disjoint deny/approve | `hard_deny_action_types` and `auto_approve_action_types` must not overlap |
| Per-task limit | `per_task_limit <= total_monthly` (when budget > 0) |
| Per-agent daily limit | `per_agent_daily_limit <= total_monthly` (when budget > 0) |
| Routing rule filter | At least one of `role_level` or `task_type` must be set per routing rule |
| Trust strategy fields | Active trust strategy must have its required config (e.g. `weighted` needs `promotion_thresholds`) |

---

## Full Example

??? example "Complete annotated configuration"

    ```yaml
    company_name: "Acme AI Corp"
    company_type: startup

    config:
      autonomy:
        level: semi

    departments:
      - name: "executive"
        budget_percent: 20
        head_role: "CEO"
        reporting_lines:
          - subordinate: "CTO"
            supervisor: "CEO"
      - name: "engineering"
        budget_percent: 60
        head_role: "CTO"
        reporting_lines:
          - subordinate: "Full-Stack Developer"
            supervisor: "CTO"
      - name: "product"
        budget_percent: 20
        head_role: "Product Manager"

    agents:
      - role: "CEO"
        name: "Alice"
        level: c_suite
        department: "executive"
        model:
          tier: "large"
          priority: "quality"
          min_context: 100000
        personality:
          openness: 0.85
          conscientiousness: 0.6
          decision_making: directive
      - role: "CTO"
        name: "Bob"
        level: c_suite
        department: "executive"
        model:
          tier: "large"
          priority: "quality"
        personality:
          openness: 0.85
          conscientiousness: 0.4
          decision_making: intuitive
      - role: "Full-Stack Developer"
        name: "Charlie"
        level: senior
        department: "engineering"
        model:
          tier: "medium"
          priority: "balanced"
        personality:
          openness: 0.5
          conscientiousness: 0.85
          decision_making: analytical
      - role: "Product Manager"
        name: "Diana"
        level: senior
        department: "product"
        model:
          tier: "medium"
          priority: "speed"
        personality:
          openness: 0.6
          conscientiousness: 0.7
          decision_making: consultative

    providers:
      cloud:
        auth_type: api_key
        api_key: "sk-example-key"
        models:
          - id: "example-large-001"
            alias: "large"
            cost_per_1k_input: 0.015
            cost_per_1k_output: 0.075
            max_context: 200000
          - id: "example-medium-001"
            alias: "medium"
            cost_per_1k_input: 0.003
            cost_per_1k_output: 0.015
          - id: "example-small-001"
            alias: "small"
            cost_per_1k_input: 0.001
            cost_per_1k_output: 0.005

    routing:
      strategy: cost_aware
      rules:
        - role_level: c_suite
          preferred_model: "large"
        - role_level: senior
          preferred_model: "medium"
          fallback: "small"
      fallback_chain:
        - "medium"
        - "small"

    budget:
      total_monthly: 200.0
      currency: "USD"
      per_task_limit: 10.0
      per_agent_daily_limit: 25.0
      alerts:
        warn_at: 75
        critical_at: 90
        hard_stop_at: 100
      auto_downgrade:
        enabled: true
        threshold: 85
        downgrade_map:
          - ["large", "medium"]
          - ["medium", "small"]

    security:
      enabled: true
      audit_enabled: true
      output_scan_policy_type: autonomy_tiered

    trust:
      strategy: disabled
      initial_level: standard

    memory:
      backend: "mem0"
      level: session
      options:
        retention_days: null  # keep forever
        max_memories_per_agent: 10000
        shared_knowledge_base: true

    task_assignment:
      strategy: role_based
      max_concurrent_tasks_per_agent: 5

    graceful_shutdown:
      grace_seconds: 30.0
      cleanup_seconds: 5.0

    workflow_handoffs:
      - from_department: "engineering"
        to_department: "product"
        trigger: "Feature completed for review"
        artifacts:
          - "pull_request"

    escalation_paths:
      - from_department: "engineering"
        to_department: "executive"
        condition: "Technical blocker requiring executive decision"
        priority_boost: 1
    ```

---

## Agent Evolution

!!! note "Evolution configuration is not loaded from company YAML"

    The evolution subsystem (`EvolutionConfig` in `synthorg.engine.evolution.config`)
    is a first-class pluggable system, but its config **is not parsed from the
    top-level company YAML**. `RootConfig` silently drops any `evolution:` block
    you write here. The service is assembled by calling
    `build_evolution_service()` from your factory or test harness with a
    fully-constructed `EvolutionConfig` object plus runtime dependencies
    (registry, versioning, tracker, optional provider, optional shadow runner).

    A REST API and dashboard UI for runtime evolution management are planned.
    Until they ship, operators who need to adjust guards, triggers, proposers,
    or adapters must do so in application code that wires the service, not in
    the company YAML.

    See [HR & Agent Lifecycle: Agent Evolution](../design/hr-lifecycle.md#agent-evolution)
    for the full architecture, the pluggable axes (triggers / proposers /
    adapters / guards), and the `ShadowEvaluationConfig` reference.

---

## See Also

- [Agent Roles & Hierarchy](agents.md): detailed agent configuration
- [Budget & Cost Control](budget.md): budget enforcement and cost tracking
- [Security & Trust Policies](security.md): trust strategies and security rules
- [Memory Configuration](memory.md): memory backends and retrieval
- [Tool Integration (MCP)](mcp-tools.md): external tool configuration
- [Design Specification](../design/index.md): full architecture reference
