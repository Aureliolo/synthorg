---
title: Security Policies
description: Configure autonomy levels, approval gates, and custom security policies.
---

# Security Policies

Every tool invocation in SynthOrg passes through the SecOps security pipeline. This guide covers how to configure autonomy levels, approval workflows, custom policies, and output scanning. For the internal architecture of the security subsystem, see the [Security](../security.md) reference.

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

## Tool Access Levels

Tool access categories map to the `ToolAccessLevel` an agent's identity carries:

| Level | Value | Access |
|-------|-------|--------|
| Sandboxed | `sandboxed` | Sandbox-only execution, no filesystem or network |
| Restricted | `restricted` | Read-only filesystem, limited network |
| Standard | `standard` | Read-write filesystem, version control, code execution |
| Elevated | `elevated` | All categories including deployment, database admin |
| Custom | `custom` | Explicit allow/deny lists (ignores the hierarchy) |

Levels form a hierarchy where each includes all categories from lower levels.

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

For actions that the rule engine cannot classify with high confidence, an LLM can provide cross-validation. Reaching for a *different provider family* than the judged agent is the point of the mechanism: a jailbreak that works on one family should not also carry its reviewer.

That is now the operator's choice to make and the system's to report on, not something it arranges. Nothing auto-selects a cross-family model, because auto-selecting one means picking a connection nobody chose. The evaluator dispatches on `security.llm_evaluator_model`, an explicit `(provider, model)` pair, and when that pair shares the judged agent's vendor family the evaluation logs `security.llm_eval.same_family_fallback` at WARNING on every call. Leaving the pair unset leaves the fallback off.

```yaml
security:
  llm_fallback:
    enabled: true
    timeout_seconds: 10.0
    max_input_tokens: 2000
    on_error: escalate
    reason_visibility: generic
    argument_truncation: per_value
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Whether LLM fallback is active |
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

This section covers runtime operations on the autonomy and tool-permission surface: promoting an agent, setting a department-level or per-initiative override, granting (or revoking) tool categories per-agent, and querying the audit trail.

### Promote or demote an agent's autonomy

Human-only. No agent (not even the CEO) can escalate privileges programmatically.

```bash
curl -X PATCH http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"autonomy_level": "semi"}'
```

Valid values: `full`, `semi`, `supervised`, `locked`.

Automatic demotions happen on: sustained high error rate (one level down), budget exhausted (`supervised`), security incident (`locked`). Recovery from auto-downgrade is human-only.

### Set a department-level override

Resolution chain: per-agent > per-initiative > per-department > company default. To set a department-wide override:

```bash
curl -X PATCH http://localhost:3001/api/v1/departments/${DEPT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"autonomy_level": "supervised"}'
```

Clear with `{"autonomy_level": null}` to remove the department override. Resolution then falls to the company default, unless a more-specific per-initiative or per-agent override still applies.

### Set a project's autonomy mode

Scopes an oversight mode to one initiative, resolved below a per-agent override and above the department/company default:

```bash
curl -X PATCH http://localhost:3001/api/v1/projects/${PROJECT_ID}/autonomy-mode \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"mode": "supervised"}'
```

Clear with `{"mode": null}` to inherit the department/company default. Setting `full` (gate-off pass-through) disables the per-action gate for the initiative's agents and is a CEO-only deliberate opt-in, so it requires `{"mode": "full", "confirm": true}` and is audited at WARNING. The write is version-guarded; pass `expected_version` to have a concurrent edit surface a 409 instead of clobbering.

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
