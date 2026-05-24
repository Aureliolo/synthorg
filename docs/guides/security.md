---
title: Security & Trust Policies
description: Configure trust strategies, autonomy levels, approval gates, and custom security policies.
---

# Security & Trust Policies

Every tool invocation in SynthOrg passes through the SecOps security pipeline. This guide covers how to configure autonomy levels, trust strategies, approval workflows, custom policies, and output scanning. For the internal architecture of the security subsystem, see the [Security](../security.md) reference.

---

## Autonomy Levels

Autonomy levels control which actions require human approval. Set the company-wide level in `config.autonomy.level`, with optional per-agent overrides:

| Level | Value | Behaviour |
|-------|-------|----------|
| Full | `full` | Agents execute all actions without approval |
| Semi | `semi` | Risky actions (deploy, db:admin, org:fire) require approval |
| Supervised | `supervised` | Most actions require approval |
| Locked | `locked` | All actions require approval |

```yaml
config:
  autonomy:
    level: semi

agents:
  - role: "Junior Developer"
    autonomy_level: supervised  # more restrictive than company default
  - role: "CEO"
    autonomy_level: full        # less restrictive than company default
```

---

## Trust Strategies

Trust strategies control how agents earn (or lose) access to higher-privilege tool categories over time. Configure via the `trust` section:

=== "Disabled"

    All agents start and remain at `initial_level`. No automatic trust progression.

    ```yaml
    trust:
      strategy: disabled
      initial_level: standard
    ```

    **When to use:** Simple setups, fully autonomous orgs, or when you manage trust externally.

=== "Weighted"

    Trust score computed from weighted factors. Agents are promoted when their score exceeds a threshold.

    ```yaml
    trust:
      strategy: weighted
      initial_level: restricted
      weights:
        task_difficulty: 0.3
        completion_rate: 0.25
        error_rate: 0.25
        human_feedback: 0.2
      promotion_thresholds:
        restricted_to_standard:
          score: 0.7
          requires_human_approval: false
        standard_to_elevated:
          score: 0.9
          requires_human_approval: true  # REQUIRED (security invariant)
    ```

    Weights must sum to 1.0 (within 0.01 tolerance).

    **When to use:** Gradual trust building based on agent performance metrics.

=== "Per-Category"

    Independent trust levels per action category. Each category can have its own promotion criteria.

    ```yaml
    trust:
      strategy: per_category
      initial_level: restricted
      initial_category_levels:
        code: restricted
        vcs: sandboxed
        deploy: sandboxed
      category_criteria:
        code:
          restricted_to_standard:
            tasks_completed: 10
            quality_score_min: 7.0
            requires_human_approval: false
          standard_to_elevated:
            tasks_completed: 50
            quality_score_min: 8.5
            requires_human_approval: true  # REQUIRED (security invariant)
        vcs:
          sandboxed_to_restricted:
            tasks_completed: 5
            quality_score_min: 7.0
    ```

    **When to use:** Fine-grained control where some action categories are more sensitive than others.

    !!! note

        Every category in `category_criteria` must have a matching entry in `initial_category_levels`. Categories with criteria but no initial level produce a validation error.

=== "Milestone"

    Gate-based trust with explicit criteria per transition.

    ```yaml
    trust:
      strategy: milestone
      initial_level: restricted
      milestones:
        restricted_to_standard:
          tasks_completed: 20
          quality_score_min: 7.5
          time_active_days: 7
          clean_history_days: 3
          auto_promote: true
          requires_human_approval: false
        standard_to_elevated:
          tasks_completed: 100
          quality_score_min: 8.5
          time_active_days: 30
          clean_history_days: 14
          auto_promote: false
          requires_human_approval: true  # REQUIRED (security invariant)
      re_verification:
        enabled: true
        interval_days: 90
        decay_on_idle_days: 30
        decay_on_error_rate: 0.15
    ```

    `auto_promote` and `requires_human_approval` are mutually exclusive per milestone.

    **When to use:** Organisations that want time-based gates and periodic re-verification.

!!! warning "Security invariant: standard_to_elevated"

    The `standard_to_elevated` transition **always requires `requires_human_approval: true`**, regardless of trust strategy. This is enforced by validation and cannot be overridden. Attempting to set `requires_human_approval: false` on this transition produces a validation error.

---

## Tool Access Levels

Trust levels map to tool access categories:

| Level | Value | Access |
|-------|-------|--------|
| Sandboxed | `sandboxed` | Sandbox-only execution, no filesystem or network |
| Restricted | `restricted` | Read-only filesystem, limited network |
| Standard | `standard` | Read-write filesystem, version control, code execution |
| Elevated | `elevated` | All categories including deployment, database admin |
| Custom | `custom` | Explicit allow/deny lists (ignores the hierarchy) |

Levels form a hierarchy where each includes all categories from lower levels.

---

## Re-verification

For the **milestone** strategy, re-verification periodically re-evaluates trust:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Whether re-verification is active |
| `interval_days` | int | `90` | Days between re-verifications |
| `decay_on_idle_days` | int | `30` | Demote one level after this many idle days |
| `decay_on_error_rate` | float | `0.15` | Demote if error rate exceeds this threshold |

---

## Security Configuration

The `security` section controls the SecOps rule engine, output scanning, and audit logging:

```yaml
security:
  enabled: true
  audit_enabled: true
  post_tool_scanning_enabled: true
  output_scan_policy_type: autonomy_tiered
  hard_deny_action_types:
    - "deploy:production"
    - "db:admin"
    - "org:fire"
  auto_approve_action_types:
    - "code:read"
    - "docs:write"
```

### Security Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Master switch for the security subsystem |
| `audit_enabled` | bool | `true` | Record audit entries for all evaluations |
| `post_tool_scanning_enabled` | bool | `true` | Scan tool output for secrets and PII |
| `hard_deny_action_types` | list | `["deploy:production", "db:admin", "org:fire"]` | Actions always denied |
| `auto_approve_action_types` | list | `["code:read", "docs:write"]` | Actions always approved |
| `output_scan_policy_type` | string | `"autonomy_tiered"` | Output scan response policy |
| `custom_policies` | list | `[]` | User-defined policy rules |

!!! warning

    `hard_deny_action_types` and `auto_approve_action_types` must not overlap. Overlapping entries produce a validation error.

---

## Rule Engine

The rule engine runs synchronous checks against every tool invocation:

```yaml
security:
  rule_engine:
    credential_patterns_enabled: true
    data_leak_detection_enabled: true
    destructive_op_detection_enabled: true
    path_traversal_detection_enabled: true
    max_argument_length: 100000
    custom_allow_bypasses_detectors: false
```

### Built-in Detectors

| Detector | Config Flag | What It Catches |
|----------|-------------|-----------------|
| Credential patterns | `credential_patterns_enabled` | API keys, passwords, tokens in arguments |
| Data leak detection | `data_leak_detection_enabled` | PII, sensitive file paths, internal URLs |
| Destructive operations | `destructive_op_detection_enabled` | `rm -rf`, `DROP TABLE`, force-push |
| Path traversal | `path_traversal_detection_enabled` | `../` sequences, path escape attempts |

Each detector can be independently enabled or disabled.

---

## Custom Security Policies

Define custom rules to allow, deny, or escalate specific action types:

```yaml
security:
  custom_policies:
    - name: "block-external-comms"
      description: "Prevent agents from sending external communications"
      action_types:
        - "comms:external"
      verdict: deny
      risk_level: high
      enabled: true
    - name: "escalate-deploys"
      description: "Escalate staging deployments for review"
      action_types:
        - "deploy:staging"
      verdict: escalate
      risk_level: medium
```

### Policy Rule Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | *(required)* | Unique rule identifier |
| `description` | string | `""` | Human-readable description |
| `action_types` | list | `[]` | Action types this rule applies to (`category:action` format) |
| `verdict` | string | `"deny"` | Verdict: `allow`, `deny`, or `escalate` |
| `risk_level` | string | `"medium"` | Risk level: `low`, `medium`, `high`, `critical` |
| `enabled` | bool | `true` | Whether this rule is active |

### Action Types

Action types follow a `category:action` format. Built-in types include:

| Category | Actions |
|----------|---------|
| `code` | `read`, `write`, `create`, `delete`, `refactor` |
| `test` | `write`, `run` |
| `docs` | `write` |
| `vcs` | `read`, `commit`, `push`, `branch` |
| `deploy` | `staging`, `production` |
| `comms` | `internal`, `external` |
| `budget` | `spend`, `exceed` |
| `org` | `hire`, `fire`, `promote` |
| `db` | `query`, `mutate`, `admin` |
| `arch` | `decide` |

!!! warning "Bypass mode restriction"

    When `custom_allow_bypasses_detectors` is `true`, custom policies are placed *before* the built-in detectors in the evaluation pipeline. In this mode, only `deny` verdicts are allowed in custom policies; `allow` and `escalate` would skip all security detectors and are rejected at validation time.

---

## LLM Security Fallback

For actions that the rule engine cannot classify with high confidence, an LLM from a *different provider family* can provide cross-validation:

```yaml
security:
  llm_fallback:
    enabled: true
    model: "example-medium-001"
    timeout_seconds: 10.0
    max_input_tokens: 2000
    on_error: escalate
    reason_visibility: generic
    argument_truncation: per_value
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Whether LLM fallback is active |
| `model` | string | `null` | Model ID (auto-selects cross-family if null) |
| `timeout_seconds` | float | `10.0` | Maximum time for the LLM call |
| `max_input_tokens` | int | `2000` | Token budget cap for eval prompts |
| `on_error` | string | `"escalate"` | Policy when LLM call fails: `use_rule_verdict`, `escalate`, `deny` |
| `reason_visibility` | string | `"generic"` | How much reason is visible: `full`, `generic`, `category` |
| `argument_truncation` | string | `"per_value"` | Truncation strategy: `whole_string`, `per_value`, `keys_and_values` |

---

## Output Scanning

After tool execution, the output scanner checks for leaked secrets and PII:

| Policy | Value | Behaviour |
|--------|-------|----------|
| Redact | `redact` | Replace matches with `[REDACTED]` and return |
| Withhold | `withhold` | Clear the entire output (fail-closed) |
| Log only | `log_only` | Log findings but pass output through |
| Autonomy-tiered | `autonomy_tiered` | Delegate response based on agent's autonomy level (default; falls back to `redact`) |

```yaml
security:
  output_scan_policy_type: autonomy_tiered
```

---

## Autonomy & Permissions (Runtime Operations)

This section covers runtime operations on the autonomy and tool-permission surface: promoting an agent, setting a department-level override, granting (or revoking) tool categories per-agent, and querying the audit trail.

### Promote or demote an agent's autonomy

Human-only. No agent (not even the CEO) can escalate privileges programmatically.

```bash
curl -X PATCH http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"autonomy_level": "semi"}'
```

Valid values: `full`, `semi`, `supervised`, `locked`. Promotion to `full` is rejected for Juniors / Interns (see [Seniority levels](../design/hr-lifecycle.md#seniority--authority-levels)).

Automatic demotions happen on: sustained high error rate (one level down), budget exhausted (`supervised`), security incident (`locked`). Recovery from auto-downgrade is human-only.

### Set a department-level override

Resolution chain: per-agent > per-department > company default. To set a department-wide override:

```bash
curl -X PATCH http://localhost:3001/api/v1/departments/${DEPT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"autonomy_level": "supervised"}'
```

Clear with `{"autonomy_level": null}` to fall back to the company default.

### Tool permission management

Per-agent tool permissions are managed via the agent's `tools.allowed` / `tools.denied` lists:

```bash
# Grant a category
curl -X PATCH http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"tools": {"allowed": ["file_system", "git", "web"], "denied": ["deployment"]}}'
```

Resolution precedence: `denied` > `allowed` > access-level default > deny.

### Audit log queries

```bash
# Last 24h of security evaluations
curl "http://localhost:3001/api/v1/security/audit?since=$(date -u -d '24 hours ago' +%s)" \
  -H "Cookie: ${SESSION}" | jq

# Filter by agent + action type
curl "http://localhost:3001/api/v1/security/audit?agent_id=${AGENT_ID}&action_type=code:create" \
  -H "Cookie: ${SESSION}" | jq

# Filter by verdict
curl "http://localhost:3001/api/v1/security/audit?verdict=DENY" \
  -H "Cookie: ${SESSION}" | jq '.data[] | {agent_id, action_type, tool_name, reason, timestamp}'
```

Supported filters: `agent_id`, `tool_name`, `verdict` (`ALLOW`, `DENY`, `ESCALATE`), `action_type`, `since`, `until`.

---

## See Also

- [Company Configuration](company-config.md): full configuration reference
- [Security](../security.md): security architecture reference
- [Design: Security & Approval](../design/security.md): security design specification
