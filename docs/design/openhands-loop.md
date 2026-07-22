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
  `LocalWorkspace` runs bash/edit/test natively inside our container;
  `ConversationState` + `EventLog` (keyed by a stable conversation id under
  a persistence dir) give task-level resume.
- **Rejected:** Goose (now Apache-2.0, but Rust, a foreign-runtime embed
  with no Python SDK); OpenCode (MIT, but a CLI/TUI with weaker
  per-action governance and non-LiteLLM routing); mini-swe-agent (great
  baseline, no governance hooks); Aider (pair-programmer, not
  autonomous); vendor `CLI` tools (reintroduce provider lock-in). None clears
  Python-embeddable + LiteLLM + event-stream governance + MCP-first at
  once.

## Adapter shape

The adapter is a thin, testable client (`engine/openhands/`,
`# module-kind: adapter`). The runtime boundary is inverted behind an
`OpenHandsConversation` protocol (`conversation.py`) so the loop logic is
unit-tested against a deterministic fake that emits scripted events; the
real runtime (`container_runtime.py`) drives a sandbox container and is
exercised end-to-end by the live smoke. The SDK never enters the main
venv: it runs only inside the container.

- `loop.py`: `OpenHandsLoop` satisfies the structural `ExecutionLoop`
  protocol; `get_loop_type()` returns `"openhands"`. `tool_invoker`,
  `provider`, `completion_config` and `streaming_enabled` are unused by
  design (OpenHands runs its own tools, and reaches models and its own
  streaming only through the gateway).
- `events.py`: the transport-neutral `OpenHandsEvent` (message / action /
  observation / finished / error). A model validator enforces that
  `tool_name` is set only on an action and token/cost figures only on a
  turn (message / action); `finish_reason` is a computed field.
- `conversation.py`: the `OpenHandsConversation` protocol, its
  `OpenHandsRunSpec` (task prompt, model, gateway + MCP base URLs, gateway
  token, workspace path, conversation id, max turns, project id), an
  `EventSink` (returns `False` to request an early stop) and the
  `ConversationFactory`. `OpenHandsOutcome` makes a natural finish and an
  error message mutually exclusive.
- `config.py`: frozen `OpenHandsLoopConfig` (max turns, token TTL, idle
  timeout) and `OpenHandsLoopDeps` (conversation factory, the **shared**
  gateway signer, gateway + MCP base URLs, clock; blank URLs are rejected
  at construction).
- `container_runtime.py`: the real runtime. It drives the injected
  `SandboxStreamer` (structurally the egress-pinned `DockerSandbox`),
  serialises the run spec to one JSON line on the container's `stdin`, and
  parses the structured JSON event stream from its `stdout`. Depends on a
  narrow protocol, not the concrete backend, so the engine stays off the
  sandbox internals.
- `errors.py`: `OpenHandsLoopError` / `OpenHandsRuntimeError` /
  `OpenHandsUnavailableError`.

## In-sandbox execution model

The agent runs to completion **inside** the `docker/openhands` container,
never in the API process: the SDK and its native `terminal` / `file_editor`
tools live only in the image. The host drives one run over the container's
standard streams (no in-container HTTP server):

- The container entrypoint (`docker/openhands/run_task.py`) reads one JSON
  run-spec line from `stdin`, builds the SDK `LLM` (pointed at the gateway
  bearer + base URL), `Agent` (native tools + the credentialed-MCP server
  over streamable-http) and a `LocalConversation` (persisting state under
  the workspace, keyed by the conversation id), and streams each agent
  event as one structured JSON line on `stdout`.
- The host-side `DockerSandbox.stream_container_task` spawns the container
  with `stdin`/`stdout` attached, writes the spec, and yields each `stdout`
  line. Egress is pinned by the network sidecar to exactly the gateway +
  credentialed-MCP hosts (the backend's `allowed_hosts`); the workspace is
  mounted read-write. The container and sidecar are torn down on every
  exit path (natural end, early stop, error, or cancellation of the
  awaiting coroutine).

## execute() flow

1. **Mint the per-run bearer** from the shared gateway signer, binding
   `(execution_id, agent_id, task_id, project_id, provider, model_id,
   cost_ceiling)`; Explicit Provider Binding is enforced at mint (an
   unbound model fails loud, never auto-picks).
2. **Build the run spec** with the gateway/MCP endpoints, the workspace
   mount path, and the stable per-task conversation id for resume.
3. **Build the conversation** via the factory: `container_runtime` bound to
   the egress-pinned sandbox. The container's `LLM(api_key=<bearer>,
   base_url=<gateway>)` reaches models only through the gateway and its
   credentialed tools only through the MCP endpoint.
4. **Stream**, consuming the container's `stdout` events. At every event
   boundary the sink consults `budget_checker` / `shutdown_checker` /
   `task_cancellation_checker` and returns `False` to stop, yielding the
   matching `TerminationReason` (`BUDGET_EXHAUSTED` / `SHUTDOWN` /
   `CANCELLED`) and tearing the container down. The gateway additionally
   enforces the authoritative hard token kill server-side.
5. **Map events to turns**: an action becomes one `TurnRecord`; a message
   advances conversation state. The container reports a running accumulated
   cost per event, so the host attributes the per-turn cost delta (the
   deltas sum to the run total; the gateway is the authoritative cost sink).
6. **Completion**: build `ExecutionResult(COMPLETED)`, then apply the
   **exact native NO_OP predicate**: a task with `artifacts_expected` that
   produced no tool calls and is not a resumed run terminates `NO_OP`
   (routed to `FAILED` downstream), never a silent success. Every terminal
   transition logs `EXECUTION_LOOP_TERMINATED`.

## Resume

Task-level. The container persists `ConversationState` + `EventLog` under
the (read-write) workspace, keyed by the stable per-task conversation id;
on a resumed run the same id re-attaches to the persisted conversation. No
per-tool-exec SynthOrg checkpoint callback is wired (that is
native-loop-specific), so `make_loop_with_callback` returns the
`OpenHandsLoop` unchanged rather than warning it is unsupported.

## Dependency isolation

`openhands-sdk` + `openhands-tools` are bundled **only in the container
image** (`docker/openhands/`, a hash-pinned lockfile), never in the main
package venv. The host needs no SDK client at all: it drives the container
over `stdin`/`stdout`, so the SDK's litellm 1.93 closure never touches the
app's pinned dependency set. (`openhands-agent-server` is deliberately
excluded: the stream model does not use it.) This keeps
`check_license_compat.py` green (image-only MIT/Apache). The runtime lives
behind the `SandboxStreamer` protocol, so the main venv never imports the
SDK.

## Selection

The loop is chosen per task-complexity through the existing loop-selection
path, with `"openhands"` registered in the loop registry and both
known/buildable frozensets. Selection is off by default: the boot wiring
builds an `AutoLoopConfig` only when `engine.loop_auto_select_enabled` is
set, from the default complexity rules merged with
`engine.loop_complexity_overrides` and the `engine.default_loop_type`
fallback, so an operator can route any complexity (or every unmatched
task) to OpenHands for an A/B. The registry factory requires
`OpenHandsLoopDeps`; without them it fails loud, so an unwired deployment
can never silently fall back to a different loop.
