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
| `execution.loop.turn_start` | `text_message_start` |
| `execution.loop.turn_complete` | `text_message_end` |
| `execution.loop.tool_calls` | `tool_call_start` |
| `approval_gate.context.parked` | `approval_interrupt` |
| `approval_gate.context.resumed` | `approval_resumed` |

Streaming events (`text_message_content`, `tool_call_args`, `tool_call_end`,
`info_request_interrupt`, `info_request_resumed`) are
emitted directly by their services via `EventStreamHub.publish_raw()`, not via
the EventProjector log projection, because they carry structured payloads that
don't originate from a single log call.

`AgUiEventType` also carries `step_started` / `step_finished` / `step_failed`,
which are absent from the table above. They are AG-UI protocol vocabulary the
enum mirrors for completeness; no execution loop emits a step, so nothing
projects onto them and the dashboard does not subscribe to them.

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

The `id` is also written as the top-level SSE `id:` framing line (not just
inside the JSON data), so a browser `EventSource` records it and replays it
as the `Last-Event-ID` request header when it reconnects. CR / LF are
stripped from the id before framing to prevent SSE field injection.

The `EventStreamHub` (`communication/event_stream/stream.py`) is the single
pub/sub source. Both the AG-UI dashboard and the A2A gateway consume
from this hub, each applying their own projection layer.

### Dashboard SSE feed (`GET /api/v1/events/dashboard`)

A second, session-less SSE endpoint
(`EventStreamController.dashboard_stream`, generator in
`api/controllers/events/_dashboard.py`) is the read-only fallback the SPA
opens when the WebSocket upgrade is proxy-blocked. Unlike `/events/stream`
(a per-task AG-UI session stream), it bridges the Litestar `ChannelsPlugin`
feed the WebSocket handler serves: it subscribes to every channel the caller
may read (`resolve_dashboard_channels`, gating budget / internal
channels by role) plus the caller's `user:{id}` channel, then forwards each
published `WsEvent` verbatim under a single named `ws` SSE frame (so the
client needs one listener, not one per event type). It emits periodic
`keepalive` frames, layers `revalidated_sse_stream` for periodic auth
revalidation, and accepts a `last_event_id` query parameter (not the native
`Last-Event-ID` header) that triggers replay of the recent per-channel
backlog (`MemoryChannelsBackend(history=20)`).

## Interrupt and Resume Protocol

Two blocking interrupt types:

**Tool Approval Interrupt**: emitted when `ApprovalGate` parks execution:

- Payload: `interrupt_id`, `tool_name`, `tool_args`, `evidence_package_id`,
  `timeout_seconds`
- Resume: `POST /api/v1/interrupts/{interrupt_id}/resume` with
  `{decision, feedback}`

**Mid-task clarification pause**: when an agent needs a human's answer
mid-task, the `request_clarification` tool (gated by
`engine.clarification_enabled`, on by default) does NOT mint a dedicated
`INFO_REQUEST` interrupt. It parks the run through the same `ApprovalGate`
machinery as a tool approval, creating an `ApprovalItem`
(`source=PARKED_CONTEXT`, `metadata.clarification=true`, plus the agent's
declared `metadata.reversibility`) and moving the task to `AWAITING_INPUT`; the
human's answer arrives through the standard approvals-decision endpoint, or
through the question-shaped door on the conversational surface
(`POST /meta/chat/questions/{approval_id}/answer`, which delegates to the same
decision write but requires a non-blank answer), and resumes the run with the
answer injected. Declining to answer resumes the run with a fixed instruction to
proceed on the agent's own judgement. See [The Org Asks](org-questions.md). The
`request_project_decision` tool (gated by `engine.scoping_enabled`, also on by
default) works the same way and additionally records the choice as a
project-brain `DECISION` entry. When the choice is
between known options, the agent supplies each with a title, a writeup of its
tradeoffs, and a single recommendation; these ride on the `ApprovalItem`'s
`EvidencePackage` (`options`, validated by the same decision-item invariants as a
plan `PlanItem`) so the operator picks **structurally** by option id rather than
typing free text. The dashboard renders the writeups and posts the pick as
`chosen_option_id` on approve; the approve controller resolves it to the option's
writeup, which becomes the decision the parked agent resumes with (and the brain
`DECISION` entry's answer). An open-ended decision (no options) keeps the
free-text answer path. `InterruptType.INFO_REQUEST` exists as scaffolding but is
not emitted.

Non-SSE polling fallback: `GET /api/v1/interrupts` +
`POST /api/v1/interrupts/{id}/resume`. Used by CLI/integration tests and
by the dashboard's Mission Control interrupts panel, which polls these
endpoints while the live WebSocket transport is down and otherwise stays
hidden (the live surface owns interrupts when connected).

## EvidencePackage Schema

`EvidencePackage` (in `core/evidence.py`, re-exported from
`communication/event_stream/evidence.py`) is the structured HITL approval
payload. It extends `StructuredArtifact` (shared base with
`HandoffArtifact`):

- `id`, `title`, `narrative`: human-readable summary
- `reasoning_trace`: compressed reasoning steps
- `recommended_actions`: 1-3 `RecommendedAction` options
- `options`, `chosen_option_id`: the decision fork for an execution-time
  decision approval. `options` is a tuple of `PlanOption` (id, title, tradeoff
  summary, recommended) validated by the shared decision-item invariants (>= 2,
  exactly one recommended, unique ids); `chosen_option_id` is the operator's pick
  and must name one of the options. Empty for a non-decision package.
- `risk_level`: `ApprovalRiskLevel`
- `source_agent_id`, `task_id`, `metadata`

`ApprovalItem.evidence_package` (optional) carries the package; existing
approval paths can adopt incrementally.

**Threshold signing**: High-risk `EvidencePackage` approvals
(`risk_level >= HIGH`) use m-of-n threshold signing via the
[Signed Audit Trail](security.md#signed-audit-trail).
`EvidencePackageSignature.algorithm` is Ed25519 (the baseline signing
arm; the `ml-dsa-65` value reserves the future quantum-safe arm), and the
`is_fully_signed` computed field checks the `signature_threshold`.
See `src/synthorg/observability/audit_chain/` for the signing
infrastructure.

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
`AgentContext.conversation`; compaction and context reset do not touch
it. The state channel
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

### Blocking Delegation (`delegate_and_await`)

The async tools above are fire-and-forget: the supervisor keeps running while a
background subagent works. Its blocking counterpart is the `delegate_and_await`
tool (Communication domain), for a task that must offload a self-contained
sub-investigation and consume its result inline. Rather than claiming a worker
slot, it runs a child agent **in-process** via a wired `SubAgentRunner`
(`InProcessSubAgentRunner` reuses `AgentEngine.run` re-entrantly), so there is no
worker slot and no cross-process deadlock:

- The runner resolves the target agent, creates a child `Task` (giving audit +
  resumability lineage through `parent_task_id`), and awaits its
  `ExecutionResult`, returning the child's final answer plus a bounded transcript
  summary.
- The child inherits budget, compaction, the `NO_OP` invariant, stakes routing,
  and checkpointing; its cost accrues under the parent's cost scope; it runs on
  its own `execution_id`.
- A depth + cycle guard walks the parent-task chain and rejects the call
  (`SubAgentDelegationDepthExceededError`) when the chain is at
  `engine.delegation_max_depth` or the target already sits in an ancestor's
  assignees (self-delegation / cycle), because each hop is a full budgeted agent
  run. `engine.delegation_max_turns` bounds the child; the tool is gated on
  `engine.delegation_enabled` **and** a wired runner, and classified
  `ActionType.ORG_DELEGATE` (HIGH risk) for the autonomy layer.

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
