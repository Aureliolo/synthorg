---
title: Event Stream and Async Delegation
description: SSE event-stream hub, AG-UI projection, human-in-the-loop interrupt/resume protocol, EvidencePackage schema, async delegation steering tools, and citation tracking.
---

# Event Stream and Async Delegation

The dashboard's observability surface and the human-in-the-loop (HITL)
interrupt/resume protocol both flow through the single `EventStreamHub`.
Async delegation builds on the same infrastructure so supervisor agents can
fan out to background subagents without blocking their own execution loop.

See also: [Communication](communication.md) (transport), [A2A Gateway](communication-a2a.md) (the other SSE consumer of the hub), [Coordination](communication-coordination.md) (loop prevention referenced from async delegation).

## AG-UI Projection Model

Internal observability events (from `observability/events/`) are projected
one-way to [AG-UI protocol](https://github.com/ag-ui-protocol/ag-ui)
standard types for external consumers. The internal event namespace remains
canonical; AG-UI is the external-facing projection only.

The `EventProjector` in `communication/event_stream/projector.py` maps
internal event constants to `AgUiEventType` values:

| Internal Event | AG-UI Type |
|---|---|
| `execution.engine.start` | `run_started` |
| `execution.engine.complete` | `run_finished` |
| `execution.engine.error` | `run_error` |
| `execution.plan.step_start` | `step_started` |
| `execution.plan.step_complete` | `step_finished` |
| `execution.plan.step_failed` | `step_failed` |
| `execution.loop.turn_start` | `text_message_start` |
| `execution.loop.turn_complete` | `text_message_end` |
| `execution.loop.tool_calls` | `tool_call_start` |
| `approval_gate.context.parked` | `approval_interrupt` |
| `approval_gate.context.resumed` | `approval_resumed` |

Streaming events (`text_message_content`, `tool_call_args`, `tool_call_end`,
`info_request_interrupt`, `info_request_resumed`) and `synthorg:dissent` are
emitted directly by their services via `EventStreamHub.publish_raw()`, not via
the EventProjector log projection, because they carry structured payloads that
don't originate from a single log call.

## SSE Endpoint

`GET /api/v1/events/stream?session_id={id}` returns a `text/event-stream`
response. Each SSE event has:

```json
{
  "id": "evt-<uuid>",
  "type": "<AgUiEventType>",
  "timestamp": "<ISO 8601>",
  "session_id": "<session>",
  "correlation_id": "<optional>",
  "agent_id": "<optional>",
  "payload": { ... }
}
```

The `EventStreamHub` (`communication/event_stream/stream.py`) is the single
pub/sub source. Both the AG-UI dashboard and the A2A gateway consume
from this hub, each applying their own projection layer.

## Interrupt / Resume Protocol

Two blocking interrupt types:

**Tool Approval Interrupt**: emitted when `ApprovalGate` parks execution:

- Payload: `interrupt_id`, `tool_name`, `tool_args`, `evidence_package_id`,
  `timeout_seconds`
- Resume: `POST /api/v1/events/resume/{interrupt_id}` with
  `{decision, feedback}`

**Information Request Interrupt**: emitted when an agent needs
mid-task clarification:

- Payload: `interrupt_id`, `question`, `context_snippet`, `timeout_seconds`
- Resume: `POST /api/v1/events/resume/{interrupt_id}` with `{response}`

Non-SSE polling fallback for CLI/integration tests:
`GET /api/v1/interrupts` + `POST /api/v1/interrupts/{id}/resume`.

## EvidencePackage Schema

`EvidencePackage` (in `core/evidence.py`, re-exported from
`communication/event_stream/evidence.py`) is the structured HITL approval
payload. It extends `StructuredArtifact` (shared base with
`HandoffArtifact` from R2 #1262):

- `id`, `title`, `narrative`: human-readable summary
- `reasoning_trace`: compressed reasoning steps
- `recommended_actions`: 1-3 `RecommendedAction` options
- `risk_level`: `ApprovalRiskLevel`
- `source_agent_id`, `task_id`, `metadata`

`ApprovalItem.evidence_package` (optional) carries the package; existing
approval paths can adopt incrementally.

**Quantum-safe signing**: High-risk `EvidencePackage` approvals
(`risk_level >= HIGH`) use m-of-n threshold signing via the
[Quantum-Safe Audit Trail](security.md#quantum-safe-audit-trail).
`EvidencePackageSignature` carries ML-DSA-65 signatures; the
`is_fully_signed` computed field checks the threshold.
See `src/synthorg/observability/audit_chain/` for the signing
infrastructure.

## DissentRecord as First-Class Message Type

`MessageType.DISSENT` promotes `DissentRecord` from a persistence-only
artifact to a typed message on the bus (S1 #1254 constraint). When
a conflict is resolved:

1. Dissent records are built for overruled positions (existing)
2. A `synthorg:dissent` SSE event is published via the `EventStreamHub`
3. `COMM_DISSENT_PUBLISHED` observability event is logged

## A2A Projection Consolidation

The `EventStreamHub` is the single event source for all consumers. The
A2A gateway subscribes to the same hub and applies A2A-specific state
mapping (see [A2A External Gateway](communication-a2a.md)) as a
separate projection layer. No second SSE backend is needed.

---

## Async Delegation

Supervisor agents manage background subagent tasks without blocking their own
execution loop. The async task protocol provides five steering tools that wrap
the existing `TaskEngine`; no parallel task system is created.

### Steering Tools

| Tool | Service Method | Effect |
|------|---------------|--------|
| `start_async_task` | `AsyncTaskService.start_async_task()` | Creates + assigns a task via `TaskEngine`, returns task ID |
| `check_async_task` | `AsyncTaskService.check_async_task()` | Projects `TaskEngine` state to `AsyncTaskStatus` |
| `update_async_task` | `AsyncTaskService.update_async_task()` | Posts `CONTEXT_INJECTION` message to executing agent via `MessageBus` |
| `cancel_async_task` | `AsyncTaskService.cancel_async_task()` | Cancels task via `TaskEngine` with reason `ASYNC_CANCEL` |
| `list_async_tasks` | `AsyncTaskService.list_async_tasks()` | Returns `(task_id, status)` pairs for child tasks by `parent_task_id` |

All five are registered under the `communication.async_tasks` namespace
and gated by `ToolPermission.DELEGATION`.

### State Channel Pattern

`AgentContext.async_task_state` is a dedicated `AsyncTaskStateChannel`
that holds `AsyncTaskRecord` entries. It is structurally separate from
`AgentContext.conversation`; compaction strategies and
`ContextResetMiddleware` (R1 #1260) do not touch it. The state channel
is projected into the agent's system prompt on each turn via
`_inject_async_task_section()`, appended after trimming so it is never
trimmed away.

### AsyncTaskService Wraps TaskEngine

`AsyncTaskService` is a thin facade over `TaskEngine`:

- Tasks are created via `TaskEngine.create_task()` with `parent_task_id`
  for lineage, then transitioned to `ASSIGNED` with the target agent
- Status is projected through `_STATUS_MAP` (internal `TaskStatus` to
  supervisor-facing `AsyncTaskStatus`)
- Context injection uses `MessageBus.send_direct()` with
  `MessageType.CONTEXT_INJECTION`
- Listing filters `TaskEngine.list_tasks()` by `parent_task_id`

### `max_delegation_rounds` on `CoordinationConfig`

Soft cap (default 3) emits `DELEGATION_ROUND_SOFT_LIMIT` warning.
Hard abort at 2x soft cap (default 6) raises `DelegationRoundLimitError`.
Prevents delegation runaway in multi-hop delegation chains.

### Citation Tracking

Research tasks need deduplicated citation tracking across parallel
sub-agent findings.

`Citation` is a frozen Pydantic model with `url` (canonical normalised
form), `title`, `first_seen_at`, `first_seen_by_agent_id`, and
`accessed_via` (tool/memory/file).

`CitationManager` is immutable (each operation returns a new instance).
It tracks citations by normalised URL, deduplicating across agents:

- `add()` normalises the URL and deduplicates against existing entries
- `render_inline()` returns `[N]` for a tracked URL
- `render_sources_section()` renders the final `## Sources` block
- `to_handoff_payload()` / `from_handoff_payload()` enable propagation
  through delegation chains via `HandoffArtifact`

URL normalisation (`normalize_url()`) lowercases scheme + host, strips
default ports, drops fragment and credentials, sorts query parameters,
strips trailing slash, and wraps IPv6 addresses in brackets.
