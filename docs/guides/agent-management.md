---
title: Agent Management
description: Hire, fire, promote, and customise agents via the REST API. Covers model assignment, rehiring from archive, and lifecycle events.
---

# Agent Management

SynthOrg treats agents as real employees: they get hired, promoted, and fired through operator workflows. This guide covers the lifecycle surface the REST API exposes (hire, update, fire), and documents the archival and rehire paths as manual two-step procedures, noting at each point which automation the DELETE flow does not yet invoke.

For the architecture (identity versioning, evolution, performance tracking), see [Agents](../design/agents.md) and [HR & Agent Lifecycle](../design/hr-lifecycle.md).

---

## Hiring

Agents are hired via `POST /api/v1/agents` with a `CreateAgentOrgRequest` body. The DTO accepts only: `name`, `role`, `department`, and the `model_provider` / `model_id` pair (both together or both omitted). There is no shared default provider to fall back on: an agent hired without a pair is created with no explicit model override, and the company's routing strategy resolves one at dispatch time from the agent's role and the task type; setting the pair explicitly pins it. Authority follows from the role's position in the reporting graph, not a per-agent level.

A gate role (`Completion Reviewer`, `Red Team`) cannot be granted through the agent MCP surface: `agents.create` and `agents.update` both refuse a gate-role assignment, because a role that judges finished work must not be handed out through the same ambient surface an agent could reach for itself. Granting or changing a gate role goes through this REST path only, under the operator's own session.

```bash
curl -X POST http://localhost:3001/api/v1/agents \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{
    "name": "Sarah Chen",
    "role": "Senior Backend Developer",
    "department": "Engineering",
    "model_provider": "example-provider",
    "model_id": "example-capable-001"
  }' | jq
```

Tool access and autonomy are **not** part of `CreateAgentOrgRequest` either: tool grants are resolved from role, department, and org config at runtime, and `autonomy_level` is set on `UpdateAgentOrgRequest` (or on the department-level endpoints) *after* the agent is hired. See [Updating an agent](#updating-an-agent).

## Updating an agent

Partial updates via `PATCH /api/v1/agents/{name}`. The server validates conflicts and domain constraints (e.g. duplicate names, missing departments); consult the OpenAPI schema for the exact accepted fields and response codes.

```bash
# Change autonomy
curl -X PATCH http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"autonomy_level": "supervised"}'

# Swap model pair (both fields must be set together)
curl -X PATCH http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"model_provider": "example-provider", "model_id": "example-expert-001"}'
```

`UpdateAgentOrgRequest` accepts only: `name`, `role`, `department`, `autonomy_level`, `model_provider`, `model_id`.

Every update creates a new `AgentIdentity` version snapshot in `agent_identity_versions`. Query the history:

```bash
curl http://localhost:3001/api/v1/agents/${AGENT_ID}/versions \
  -H "Cookie: ${SESSION}" | jq '.data[] | {version, content_hash, saved_by, saved_at}'

# Diff two versions
curl "http://localhost:3001/api/v1/agents/${AGENT_ID}/versions/diff?from_version=1&to_version=3" \
  -H "Cookie: ${SESSION}" | jq '.data.field_changes[] | {field_path, change_type, old_value, new_value}'

# Rollback
curl -X POST http://localhost:3001/api/v1/agents/${AGENT_ID}/versions/rollback \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{"target_version": 1, "reason": "v2 overshot on autonomy"}' | jq
```

## Firing

Firing is a CRITICAL-risk operation requiring human approval by default. The pruning service can also propose fires based on performance trends.

```bash
curl -X DELETE http://localhost:3001/api/v1/agents/${AGENT_NAME} \
  -H "Cookie: ${SESSION}"
```

The `DELETE /api/v1/agents/{agent_name}` endpoint does not accept a request body. Approval metadata (reason, justification) is recorded separately when the `CRITICAL`-risk approval gate captures the decision.

Current behaviour (`delete_agent` in `src/synthorg/api/services/_org_agent_mutations.py`):

1. The API validates the agent exists and runs org-mutation guard checks.
2. The agent record is removed from the active org configuration.
3. A company snapshot is persisted and an `API_AGENT_DELETED` event is logged and broadcast on the `agents` WebSocket channel.

Not yet wired into the DELETE flow: automated task reassignment via `TaskReassignmentStrategy` (concrete: `QueueReturnStrategy` in `src/synthorg/hr/queue_return_strategy.py`), memory archival via `MemoryArchivalStrategy` (concrete: `FullSnapshotStrategy` in `src/synthorg/hr/full_snapshot_strategy.py`), selective promotion to `OrgMemoryBackend`, and an explicit `TERMINATED` lifecycle state. The strategies exist as part of the offboarding-service shape but the API DELETE handler does not invoke them; fires should be paired with manual task reassignment before the DELETE call.

## Rehiring from archive

A dedicated `POST /api/v1/agents/{agent_name}/rehire` endpoint (which would restore archived memory into a new identity with a fresh hire date and version chain) is not implemented in the agents controller. Rehiring is a manual two-step today: list archived agents via the existing listing, then recreate with `POST /api/v1/agents` using a fresh `CreateAgentOrgRequest` payload; memory restoration is performed out-of-band through the Memory Admin API. The endpoint sits alongside the same lifecycle automation called out in [Firing](#firing).

## Lifecycle events (WebSocket)

Subscribe to the `agents` channel to get real-time lifecycle events:

```javascript
ws.send(JSON.stringify({ action: 'subscribe', channels: ['agents'] }))
// Actually emitted on the `agents` channel today (see
// src/synthorg/api/controllers/agents.py and app_helpers.py):
//   agent.created, agent.updated, agent.deleted
```

See [Notifications & Events](notifications-and-events.md) for the full protocol.

## Setup wizard shortcuts

`/api/v1/setup/*` is the first-run wizard. Template-based auto-creation happens at `POST /api/v1/setup/company` when a template is selected; the wizard hydrates the org with the template's default agents in one shot. For the "Start Blank" path, `POST /api/v1/setup/agent` creates a single agent with an explicit model assignment:

```bash
# Create one agent on the Start Blank path; model assignment is required.
curl -X POST http://localhost:3001/api/v1/setup/agent \
  -H "Content-Type: application/json" \
  -H "Cookie: ${SESSION}" \
  -d '{
    "role": "Senior Backend Developer",
    "name": "Sarah Chen",
    "model_provider": "example-provider",
    "model_id": "example-capable-001"
  }'
```

After the wizard completes, use `/api/v1/agents` for subsequent changes.

---

## See Also

- [Agent Roles & Hierarchy](agents.md): role catalog, reporting-graph authority
- [Design: Agents](../design/agents.md): identity card, the bound unit, identity versioning
- [Design: HR & Agent Lifecycle](../design/hr-lifecycle.md): full lifecycle, performance tracking, evolution
- [Security Policies](security.md): autonomy and tool permissions
