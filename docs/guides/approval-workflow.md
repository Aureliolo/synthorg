---
title: Approval Workflow
description: Configure the approval gate, route requests to the right reviewer, observe the audit chain.
---

# Approval Workflow

The approval gate is SynthOrg's human-in-the-loop control surface: certain actions (deploy to production, rotate a secret, kill a runaway agent) pause until an authorised operator approves. The gate lives at `synthorg.engine.approval_gate` and integrates with the audit chain so every decision is signed and chained.

## Concepts

- **Escalation**: a structured request that an action requires approval. Carries `approval_id`, `action_type`, `agent_id`, `task_id`, and rationale.
- **Parked context**: the agent state frozen while an escalation is pending (`ParkedContext` in `synthorg.execution.parked_context`).
- **Approval verdict**: `approve` / `reject` / `request_changes` with an actor identity and timestamp.
- **Park service**: stores parked contexts, supplies their `id` and `approval_id`.

## Configuration

| Key | Type | Default | Purpose |
|---|---|---|---|
| `approval.enabled` | bool | `true` | Master switch. |
| `approval.timeout_seconds` | int | `86400` | Auto-reject pending requests after this window. |
| `approval.reviewer_groups` | list[str] | `[]` | Identity-aware roles allowed to decide. |
| `approval.notification_target` | str | (unset) | Where to push pending-approval alerts. |
| `approval.audit_chain_signing` | bool | `true` | Sign verdicts into the audit chain. |

## Worked example: a manual approval round-trip

The agent emits a pre-tool escalation:

```python
from synthorg.engine.approval_gate import ApprovalGate

gate: ApprovalGate = app_state.approval_gate
parked = await gate.park_context(
    escalation=escalation,
    context=task_context,
    agent_id="agent-007",
    task_id="123e4567-e89b-12d3-a456-426614174000",
)
print(parked.id, parked.approval_id)
```

The dashboard at `/dashboard/approvals` lists pending requests. Reviewer clicks `Approve`; the API persists a verdict:

```bash
curl -X POST http://localhost:8000/api/v1/approvals/7c9e6679-7425-40de-944b-e07fc1f90ae7/decide \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"verdict": "approve", "rationale": "Looks good; canary signal is clean."}'
```

The gate unparks the context and resumes the agent loop. The audit chain records:

1. `api.approval.created` at park time (one row per pending approval).
2. `security.approval.approved` / `security.approval.rejected` at verdict time, with the reviewer identity.
3. `approval.status_transitioned` AFTER persistence write.

## Operator surface

The approvals page surfaces pending requests with:

- Action type, agent, task, requested change.
- Time-since-raised badge (counts toward the auto-reject deadline).
- One-click `Approve` / `Reject` / `Request changes` actions; rejection requires a rationale.
- Filters on `action_type`, `agent`, `actor` (last reviewer).

For terminal automation, the MCP tool `approvals.decide` accepts the same verdict payload.

## Observability

- `api.approval.created` (info): one per pending approval row written to the store.
- `security.approval.approved` / `security.approval.rejected` (info): one per verdict; carries `actor_id` and either the rationale or the approval payload.
- `approval.status_transitioned` (info): AFTER persistence write, with `from_status` and `to_status`.
- `api.approval.expired` (warning): emitted on timeout-driven auto-rejection alongside the persistence write.

The `synthorg_approval_decisions_total` counter has bounded label `outcome` in `VALID_APPROVAL_OUTCOMES` (`approve` / `reject` / `request_changes` / `auto_rejected`).

## Audit chain

Every verdict is appended to the audit chain via the typed-boundary `audit_chain` (see [docs/reference/typed-boundaries.md](../reference/typed-boundaries.md)). The chain is hash-linked so a tampered verdict breaks downstream verification.

Operator verification:

```bash
curl -s http://localhost:8000/api/v1/audit-chain/verify \
  -H "Authorization: Bearer $TOKEN" | jq
# {"status": "valid", "appends_total": 4271, "depth": 4271}
```

A `broken` status surfaces the first divergent index plus the diverging append; either repair via the documented `audit_chain.repair` workflow or restore from a signed backup.

## Threat model

The approval gate's reliance on identity-aware reviewers means the surrounding auth surface MUST be tight:

- JWT validation at the controller (`parse_typed("jwt", ...)`).
- Reviewer-group membership checked AT decide time, not just at session start.
- Audit chain enabled in production (an unsigned audit log silently loses tamper evidence).

See [docs/reference/sec-prompt-safety.md](../reference/sec-prompt-safety.md) for the redaction rules around the rationale payload.
