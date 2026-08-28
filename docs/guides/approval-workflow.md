---
title: Approval Workflow
description: Configure the approval gate, route requests to the right reviewer, observe the audit chain.
---

# Approval Workflow

The approval gate is SynthOrg's human-in-the-loop control surface: certain actions (deploy to production, rotate a secret, kill a runaway agent) pause until an authorised operator approves. The gate lives at `synthorg.engine.approval_gate` and integrates with the audit chain so every decision is signed and chained.

## Concepts

- **Escalation**: a structured request that an action requires approval. Carries `approval_id`, `tool_name`, `action_type`, `risk_level`, and a `reason` (`EscalationInfo` in `synthorg.approval.models`); `agent_id` and `task_id` are supplied separately when the agent parks.
- **Parked context**: the agent state frozen while an escalation is pending (`ParkedContext`, serialised via the park service).
- **Approval item**: the REST-visible queue entry (`ApprovalItem` in `synthorg.core.approval`), with `status`, `risk_level`, `decided_by`, and `decision_reason`.
- **Approval verdict**: `approved` / `rejected`, with an actor identity, timestamp, and reason.

## Configuration

There is no dedicated `approval.*` settings namespace; the gate is governed by the
`security` namespace and by the security policy document:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `security.enabled` | bool | `true` | Master switch for the security subsystem, including the approval gate. |
| `security.audit_enabled` | bool | `true` | Whether security audit entries (including approval decisions) are recorded. |
| `security.timeout_check_interval_seconds` | float | `60.0` | How often the approval-timeout scheduler scans pending approvals and applies the timeout policy. |
| `observability.audit_chain_signing_timeout_seconds` | float | `5.0` | Timeout for signing and timestamping an audit-chain entry. |

Who may approve or reject is **not** a configurable role list: the REST endpoints are
guarded to the CEO, Manager, and Board Member human roles (`require_approval_roles` in
`synthorg.api.guards`).

Two independent timeout mechanisms apply:

- **Item expiry**: an approval item may carry its own `ttl_seconds` at creation (60 s to
  7 days); an item created without one never expires on its own. A read past
  `expires_at` lazily moves the item to `expired` and logs `api.approval.expired`.
- **The timeout scheduler**: polls PENDING items on `security.timeout_check_interval_seconds`
  and applies a configurable timeout policy: wait indefinitely, deny outright, or a
  per-risk-tier policy that can approve, deny, or escalate through a chain (auto-approval
  is always overridden to deny for `HIGH` and `CRITICAL` risk). A policy resolution moves
  the item straight to `approved` or `rejected`, attributed to the timeout-policy system
  actor rather than a human reviewer.

The active policy is part of the security policy document, viewed and edited via
`GET`/`POST /api/v1/settings/security/export` and `/import` or the dashboard's Security
settings page, not a flat `approval.*` key.

## Worked example: a manual approval round-trip

The agent emits a pre-tool escalation:

```python
from synthorg.approval.state import ApprovalStateSlice
from synthorg.engine.approval_gate import ApprovalGate

gate: ApprovalGate = app_state.slice(ApprovalStateSlice).gate
parked = await gate.park_context(
    escalation=escalation,
    context=task_context,
    agent_id="agent-007",
    task_id="123e4567-e89b-12d3-a456-426614174000",
)
print(parked.id, parked.approval_id)
```

The dashboard at `/approvals` lists pending requests. The reviewer clicks `Approve`; the
API persists a verdict via the dedicated `/approve` endpoint (optional `comment`, plus a
`chosen_option_id` when the escalation offered options) or `/reject` endpoint (mandatory
`reason`). Both endpoints require a caller-supplied `Idempotency-Key` header, so a retried
request returns the original decision rather than deciding twice:

```bash
curl -s -b cookies.txt -X POST http://localhost:3001/api/v1/approvals/7c9e6679-7425-40de-944b-e07fc1f90ae7/approve \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 3f6a1e2a-9c3b-4b2e-8f2b-9a2e6b1c7d10" \
  --data '{"comment": "Looks good; canary signal is clean."}'
```

The gate unparks the context and resumes the agent loop. The observability stream records:

1. `api.approval.created` at park time (one row per pending approval).
2. `security.approval.approved` / `security.approval.rejected` at verdict time, with the reviewer identity.
3. `approval.status_transitioned` immediately after the persistence write.

`security.*`-prefixed events (including the two approval verdict events above) are picked
up automatically by the audit-chain sink and signed into the hash chain; see
[Audit chain](#audit-chain) below.

## Operator surface

The approvals page surfaces pending requests with:

- Action type, agent, task, requested change.
- Time-since-raised urgency badge, thresholded by risk level.
- One-click `Approve` / `Reject` actions, plus an option picker when the escalation
  offered a structured choice; rejection requires a reason.
- Filters on `status`, `risk_level`, `action_type`, and `source`.

For terminal automation, the MCP `approvals` domain exposes `approve` and `reject` tools
that take the same approval id. Both are destructive, admin-guardrailed tools: each
requires `confirm: true` and a non-blank `reason`, refused at the schema level if either
is missing. Neither tool can answer a question an agent parked for a human; that goes
through the chat question surface instead.

## Observability

- `api.approval.created` (info): one per pending approval row written to the store.
- `security.approval.approved` / `security.approval.rejected` (info): one per verdict.
- `approval.status_transitioned` (info): after the persistence write, with `from_status`
  and `to_status`; also emitted for a timeout-scheduler resolution.
- `api.approval.expired` (warning): emitted when a read finds an item past its own
  `ttl_seconds`.

The `synthorg_approval_decisions_total` counter has a bounded `outcome` label in
`VALID_APPROVAL_OUTCOMES`: `approved`, `rejected`, `expired`.

## Audit chain

`security.*`-prefixed log events, including both approval verdict events, are captured by
`AuditChainSink` (`synthorg.observability.audit_chain`), a logging handler that signs each
one and appends it to a hash chain: each entry's signature and hash are bound to the prior
entry, so a tampered entry breaks the chain from that point on. A trusted timestamp is
requested per entry where a TSA is configured; the append still proceeds on a TSA failure,
recorded as a `fallback` rather than a `signed` append. Entries are hydrated from durable
storage at startup so verification survives a restart.

Operators query the recorded security audit trail (filterable by `agent_id`, `tool_name`, `action_type`, `verdict`):

```bash
curl -s -b cookies.txt "http://localhost:3001/api/v1/security/audit?verdict=APPROVED" | jq
```

## Threat model

The approval gate's reliance on identity-aware reviewers means the surrounding auth surface MUST be tight:

- Session validation at the controller.
- Approval-role membership (`require_approval_roles`) checked AT decide time, not just at session start.
- `security.audit_enabled` left on in production (a disabled audit log silently loses tamper evidence for the events the chain would otherwise capture).

See [docs/reference/sec-prompt-safety.md](../reference/sec-prompt-safety.md) for the redaction rules around the rationale payload.
