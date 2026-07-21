# OpenHands execution loop

OpenHands is a selectable **fourth inner execution loop**, alongside the
native `react` / `plan_execute` / `hybrid` loops behind the
`ExecutionLoop` protocol. The inner coding loop is commodity; SynthOrg's
product is the orchestration, routing and control plane around it. So
rather than treat the native loop as the only option, we bundle a
best-in-class open coder as a second, selectable inner loop and A/B them
end to end, promoting the winner. This page covers the adapter; its two
governance boundaries are the [LLM gateway](llm-gateway.md) and the
[credentialed-tool MCP server](credentialed-mcp.md).

## Phase 0: harness survey (July 2026)

The pick was re-validated against the current field before building.
**OpenHands V1 `openhands-sdk` is the confirmed choice**, with no shift
and no licence change (the whole field is MIT/Apache).

- **OpenHands V1 software-agent-sdk** (`openhands-sdk`, MIT, Python
  >=3.12): `LLM(model=, api_key=, base_url=)` points straight at our
  gateway; the append-only `EventLog` with an `on_event` callback is the
  exact seam to map to `TurnRecord`s and consult budget/shutdown/
  cancellation checkers; MCP tools are first-class (FastMCP) so it
  consumes our credentialed-MCP endpoint natively; `SecurityAnalyzer` +
  `ConfirmationPolicy` give per-action defence-in-depth; a
  workspace runs bash/edit/test natively inside our container;
  `ConversationState` + `EventLog` give task-level resume; a
  `RemoteConversation` drives an in-container `agent_server` over REST/WS.
- **Rejected:** Goose (now Apache-2.0, but Rust, a foreign-runtime embed
  with no Python SDK); OpenCode (MIT, but a CLI/TUI with weaker
  per-action governance and non-LiteLLM routing); mini-swe-agent (great
  baseline, no governance hooks); Aider (pair-programmer, not
  autonomous); vendor `CLI` tools (reintroduce provider lock-in). None clears
  Python-embeddable + LiteLLM + event-stream governance + MCP-first at
  once.

## Adapter shape

The adapter is a thin, testable client (`engine/openhands/`,
`# module-kind: adapter`). The SDK boundary is inverted behind an
`OpenHandsConversation` protocol (`conversation.py`) so the loop logic is
unit-tested against a deterministic fake that emits scripted events, and
the real SDK glue (`sdk_runtime.py`) is exercised only by the live smoke.

- `loop.py`: `OpenHandsLoop` satisfies the structural `ExecutionLoop`
  protocol; `get_loop_type()` returns `"openhands"`. `tool_invoker`,
  `provider` and `completion_config` are unused by design (OpenHands runs
  its own tools and reaches models only through the gateway).
- `events.py`: the transport-neutral `OpenHandsEvent` (message / action /
  observation / finished / error, with token and cost fields).
- `conversation.py`: the `OpenHandsConversation` protocol, its
  `OpenHandsRunSpec` (task prompt, model, gateway + MCP base URLs, gateway
  token, workspace path, max turns), an `EventSink` (returns `False` to
  request an early stop) and the `ConversationFactory`.
- `config.py`: frozen `OpenHandsLoopConfig` (max turns, token TTL) and
  `OpenHandsLoopDeps` (conversation factory, the **shared** gateway
  signer, gateway + MCP base URLs, clock).
- `sdk_runtime.py`: the real `openhands-sdk` conversation factory,
  image-only and import-guarded.
- `errors.py`: `OpenHandsLoopError` / `OpenHandsRuntimeError` /
  `OpenHandsUnavailableError`.

## execute() flow

1. **Mint the per-run bearer** from the shared gateway signer, binding
   `(execution_id, agent_id, task_id, project_id, provider, model_id,
   cost_ceiling)`; Explicit Provider Binding is enforced at mint. Unset
   gateway/MCP URLs fail loud (`OpenHandsUnavailableError`).
2. **Build the run spec** with the gateway/MCP endpoints and the workspace
   mounted at the in-container path.
3. **Build the conversation** via the factory: in the live path a
   `RemoteConversation` against the in-sandbox `agent_server`, configured
   with `LLM(api_key=<bearer>, base_url=<gateway>)` and the MCP tools from
   our credentialed endpoint; egress is locked to exactly the gateway +
   MCP hosts, everything else stays `network:"none"`.
4. **Run**, consuming the event stream. At every event boundary the sink
   consults `budget_checker` / `shutdown_checker` /
   `task_cancellation_checker` and returns `False` to stop, yielding the
   matching `TerminationReason` (`BUDGET_EXHAUSTED` / `SHUTDOWN` /
   `CANCELLED`). Budget is enforced at the boundary, matching the native
   `run_hard_ceiling` semantics.
5. **Map events to turns**: an action plus its observation becomes one
   `TurnRecord` (tokens and cost from the gateway `usage` echo, not the
   event); a message advances conversation state.
6. **Completion**: build `ExecutionResult(COMPLETED)`, then apply the
   **exact native NO_OP predicate**: a task with `artifacts_expected` that
   produced no tool calls and is not a resumed run terminates `NO_OP`
   (routed to `FAILED` downstream), never a silent success.

## Resume

Task-level. OpenHands persists `ConversationState` + `EventLog` to the
workspace volume; on resume the adapter re-attaches to the persisted
conversation. No per-tool-exec SynthOrg checkpoint callback is wired
(that is native-loop-specific), so `make_loop_with_callback` returns the
`OpenHandsLoop` unchanged rather than warning it is unsupported.

## Dependency isolation

`openhands-sdk` + `agent_server` are bundled **only in the container
image** (`docker/openhands/`), never in the main package venv. The main
package needs only an HTTP/WS client to drive the `RemoteConversation`.
This sidesteps the litellm / pyo3-3.14 pin holds entirely and keeps
`check_license_compat.py` green (image-only MIT/Apache). `sdk_runtime.py`
guards its `openhands` import and raises `OpenHandsUnavailableError` when
the SDK is absent, so the main venv never imports it.

## Selection

The loop is chosen per agent-role / task-complexity through the existing
loop-selection path, with `"openhands"` registered in the loop registry
and both known/buildable frozensets. The registry factory requires
`OpenHandsLoopDeps`; without them it fails loud, so an unwired deployment
can never silently fall back to a different loop.
