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

## Conversational chat modes

The conversational organisation interface exposes four opt-in modes on the
`/meta/chat/*` endpoints. Every mode is off by default and is built by an
enforced factory that returns `503` when a required dependency is absent, so a
half-wired deployment fails loudly rather than acting ungoverned.

| Mode | Setting | What it does | Fails closed when |
|---|---|---|---|
| Propose | `propose_enabled` | Turns a chat turn into a clarify-or-park work item routed through the work pipeline at approval time. | No provider or no connected persistence. |
| Routing | `routing_enabled` | Routes each turn to the most-senior relevant role agent (`llm` or `keyword` strategy). | Required collaborators absent. |
| Group chat and invite | `group_chat_enabled`, `invite_enabled` | Multi-party chat with on-demand agent invitations gated by an approval store. | Group-chat service or approval store absent. |
| Direct MCP acting | `direct_mcp_enabled` | Lets the conversation invoke permitted MCP write and admin actions directly. | Security governance is absent (the builder returns `None`, so the endpoint `503`s); without governance the escalate-and-park step is missing and writes would run ungated. |

Approval decisions made in conversation route through the resume-intent signal,
flowing from conversational intake through the invite step and any parked
context into the review gate. Per-conversation turns are serialised by a lock
registry, and human-supplied content is wrapped as untrusted before it reaches
any prompt.
