---
search:
  exclude: true
---

# Meta

Meta-orchestration subsystems: charter interviews, governance, and the
conversational organisation interface. The charter subsystem (which produces
a project charter from a deep interview) is documented below; other meta
surfaces are internal wiring.

## Charter Enums

::: synthorg.meta.charter.enums

## Charter Models

::: synthorg.meta.charter.models

## Charter Service

::: synthorg.meta.charter.service

## Conversational organisation interface

The operator talks to the whole organisation through **one** surface,
`POST /meta/chat/turn`. The server classifies each message into a capability
and dispatches it: the front door is a single conversation, never a set of
modes the operator must pick between. An EXPLAIN turn may also stream token by
token over `POST /meta/chat/turn/stream` (EXPLAIN only; every other intent
defers back to the buffered endpoint so an acting turn is never streamed).

Each capability keeps its own opt-in gate, model, rate limit, and downstream
state machine underneath the one surface; a turn that lands on a disabled
capability returns `503` (fails closed) rather than being answered as something
weaker. Uncertainty degrades toward `explain` (a read), never toward `act`
(a write) or `charter`.

| Capability | Setting | What a turn does | Fails closed when |
|---|---|---|---|
| Explain | `explain_chat_enabled` | Answers a read-only question about org state, grounded in a live snapshot. Streams when called via `/turn/stream`. | No provider (the chat backend is not built). |
| Propose | `propose_enabled` | Clarifies a request or drafts ONE durable `Plan` parked for holistic review. | No provider or no connected persistence. |
| Group convene | `group_chat_enabled`, `invite_enabled` | Convenes several role agents in one thread; agent-initiated invites are gated by an approval store. | Group-chat service or approval store absent. |
| Direct MCP acting | `direct_mcp_enabled` | Runs permitted MCP write/admin actions directly. Buffered + idempotent, never streamed. | Security governance is absent (fail-closed: the actor is not built, so the turn `503`s). |
| Charter | (charter substrate) | Interviews the operator to draft a company charter, with a live draft alongside. | No provider or no connected persistence. |

Concern routing (`routing_enabled`) picks which role agent answers an explain
or propose turn, and transparent multi-voice (`multi_voice_enabled`, opt-out /
default on) lets specialists add a short attributed chime-in. Approval
decisions made in conversation route through the resume-intent signal into the
review gate. Per-conversation turns are serialised by a lock registry, and
human-supplied content is wrapped as untrusted before it reaches any prompt.
