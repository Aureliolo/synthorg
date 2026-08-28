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
| Full | `full` | Approval routing is off; the deny list and the built-in detectors still run |
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
| Sandboxed | `sandboxed` | Sandbox-only execution with no filesystem or network |
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
    mcp_destructive_op_detection_enabled: true
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
| MCP destructive operations | `mcp_destructive_op_detection_enabled` | Delete, purge, and revoke calls against a third-party MCP server, which the shell and SQL detector cannot see |
| Path traversal | `path_traversal_detection_enabled` | `../` sequences, path escape attempts |

Each detector defaults to on and can be independently disabled. The policy
validator runs ahead of all of them and is not optional.

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

Action types follow a `category:action` format. The built-in taxonomy is
`ActionType` in `src/synthorg/security/autonomy/enums.py`:

| Category | Actions |
|----------|---------|
| `code` | `read`, `write`, `create`, `delete`, `refactor` |
| `test` | `write`, `run` |
| `docs` | `write` |
| `design` | `generate`, `delete` |
| `vcs` | `read`, `commit`, `push`, `branch` |
| `deploy` | `staging`, `production` |
| `publish` | `staging`, `production` |
| `comms` | `internal`, `external` |
| `budget` | `spend`, `exceed` |
| `org` | `hire`, `fire`, `promote`, `delegate` |
| `db` | `query`, `mutate`, `admin` |
| `arch` | `decide` |
| `tool` | `create` |
| `memory` | `read` |
| `knowledge` | `ingest`, `reindex` |
| `browser` | `navigate`, `screenshot`, `diff`, `accessibility_scan`, `spec` |
| `external_data` | `request` |
| `research` | `run` |
| `desktop` | `launch`, `click`, `type`, `key`, `screenshot`, `scroll` |

!!! warning "A bare category grant expands to whatever the taxonomy holds"

    An autonomy preset grants categories, not concrete types, so its
    auto-approved set is whatever `category:*` expands to when the resolver
    runs, and a type added later joins it with nobody deciding. Every concrete
    type a built-in preset may auto-approve therefore has to be declared in
    `WORKTREE_CONFINED_ACTION_TYPES` (`src/synthorg/security/action_types.py`),
    which is a claim about where the action *lands* rather than how the verb
    sounds: `code:delete` qualifies because it deletes a file in a throwaway
    worktree, and `design:delete` does not because the asset store outlives the
    run. Expansion for auto-approval covers built-in types only, since a grant
    cannot have meant a custom type registered after it was written.

!!! warning "Bypass mode restriction"

    When `custom_allow_bypasses_detectors` is `true`, custom policies are placed *before* the built-in detectors in the evaluation pipeline. In this mode, only `deny` verdicts are allowed in custom policies; `allow` and `escalate` would skip all security detectors and are rejected at validation time.

---

## LLM Security Fallback

For actions that the rule engine cannot classify with high confidence, an LLM can provide cross-validation. Reaching for a *different provider family* than the judged agent is the point of the mechanism: a jailbreak that works on one family should not also carry its reviewer.

That is now the operator's choice to make and the system's to report on, not something it arranges. Nothing auto-selects a cross-family model, because auto-selecting one means picking a connection nobody chose. The evaluator dispatches on `security.llm_evaluator_model`, an explicit `(provider, model)` pair, and when that pair shares the judged agent's vendor family the evaluation logs `security.llm_eval.same_family` at WARNING on every call. Leaving the pair unset leaves the evaluation off.

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

Human-only, on a dedicated endpoint guarded by `require_ceo_or_manager`. The
request body carries a mandatory `reason` (at least three non-whitespace
characters), which lands on the audit trail and, where the configured change
strategy requires one, on the approval item the request opens rather than
applying at once.

```bash
curl -X POST http://localhost:3001/api/v1/agents/${AGENT_ID}/autonomy \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"level": "semi", "reason": "Handing routine deploys back after a clean fortnight"}'
```

Valid levels: `full`, `semi`, `supervised`, `locked`. `GET` on the same path
reads the effective level back. The path parameter is the agent's stable id, not
its name.

`PATCH /api/v1/agents/{agent_id}` also accepts an `autonomy_level`, but that
edits the org configuration rather than performing a runtime autonomy change,
and it takes no reason.

Automatic demotions happen on four declared reasons. A sustained high error rate steps the agent down exactly one level from wherever it is (`full` to `semi` to `supervised` to `locked`), because a noisy run is a graded signal. Budget exhaustion and risk-budget exhaustion drop it to a fixed floor of `supervised`, and a security incident to `locked`, regardless of the level it held. Recovery from an auto-downgrade is human-only.

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

Per-agent tool permissions are declared on the identity, in the company
configuration, and applied at bootstrap. There is no REST or MCP write surface
for them: `PATCH /api/v1/agents/{agent_id}` forbids unknown fields and carries no
`tools` key, so a request shaped like one is rejected rather than silently
ignored.

```yaml
agents:
  - role: "Junior Developer"
    tools:
      access_level: standard
      allowed: ["file_system", "git", "web"]
      denied: ["deployment"]
      denied_categories: ["deploy"]
      mcp_capabilities: ["tasks:read"]
```

`denied_categories` exists because a name list goes stale the moment a tool joins
the category: an identity that must not reach a whole class of tool says so by
category and stays correct as the category grows.

Resolution runs in order and stops at the first match: a name in `denied` is
denied, then a name in `allowed` is allowed, then a `custom` access level denies
everything it did not name, then category gating decides (which is where
`denied_categories` applies), and anything unmatched is denied. Because the name
lists are consulted before category gating, one entry in `allowed` readmits a
single tool from an otherwise withheld category. Name matching is
case-insensitive.

### Audit log queries

`since` and `until` take ISO 8601 timestamps, not Unix epochs, and `verdict` is
lowercase.

```bash
# The last day of security evaluations
curl "http://localhost:3001/api/v1/security/audit?since=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)" \
  -H "Cookie: ${SESSION}" | jq

# Filter by agent + action type
curl "http://localhost:3001/api/v1/security/audit?agent_id=${AGENT_ID}&action_type=code:create" \
  -H "Cookie: ${SESSION}" | jq

# Filter by verdict
curl "http://localhost:3001/api/v1/security/audit?verdict=deny" \
  -H "Cookie: ${SESSION}" | jq '.data[] | {agent_id, action_type, tool_name, reason, timestamp}'
```

Supported filters, all AND-combined, results newest-first: `agent_id`,
`tool_name`, `action_type` (which must match `category:action`), `verdict`
(`allow`, `deny`, `escalate`, `output_scan`), `since`, and `until`. The response
is cursor-paginated via `cursor` and `limit`. Two further filters,
`jsonb_contains` and `jsonb_key_exists`, run containment and key-existence
queries against the `matched_rules` column and need a PostgreSQL backend; on
SQLite they answer `422`.

---

## See Also

- [Company Configuration](company-config.md): full configuration reference
- [Security](../security.md): security architecture reference
- [Design: Security & Approval](../design/security.md): security design specification
