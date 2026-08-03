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
  sandbox internals. `stdout` is a **protocol, not a log**: the entrypoint
  claims the real descriptor as a private event channel and redirects
  everything else to `stderr`, so neither the SDK's console visualizer nor a
  stray print anywhere in its dependency closure can interleave prose with the
  event lines.
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
  event as one structured JSON line on `stdout`. The model id is prefixed for
  LiteLLM routing before it reaches the SDK: LiteLLM dispatches on a provider
  prefix and the SDK forwards the name verbatim, so a bare SynthOrg model id
  resolves to no provider and never reaches `base_url` at all. The prefix
  names the wire protocol (an OpenAI-compatible proxy at `api_base`), which is
  what the gateway is; the real `(provider, model)` still comes from the run
  bearer's claims, and the gateway ignores the request's `model` field.
- The host-side `DockerSandbox.stream_container_task` spawns the container
  with `stdin`/`stdout` attached, writes the spec, and yields each `stdout`
  line. Egress is pinned by the network sidecar to the gateway +
  credentialed-MCP endpoints, at two layers: `allowed_hosts` permits the
  backend's `host:port`, and `allowed_paths` narrows that destination to
  each endpoint's own URL path prefix, enforced per HTTP request. The
  second layer is load-bearing rather than defence in depth: both endpoints
  live in the same backend process as its authentication, metrics and
  webhook routes, so a `host:port` allowlist alone would grant every one of
  them, including the routes auth middleware deliberately excludes. TLS to
  a narrowed destination is refused outright, since its paths are
  unreadable. The workspace is mounted read-write. The container and sidecar are torn down on every
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
   advances conversation state. The container reports running accumulated cost
   and token usage per event, so the host attributes the per-turn deltas (which
   sum to the run totals; the gateway is the authoritative cost sink). The
   token figures are load-bearing rather than decoration: the
   [A/B rubric](loop-ab-harness.md) ranks loops on tokens and scores an
   observed zero as unbeatable, so a run reporting none would win that
   dimension by reporting nothing at all.
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

## Operational reachability

The capability ships **on** (`tools.openhands_enabled`, which carries
`providers.gateway_enabled` with it), and everything it needs is provided
rather than left for an operator to discover:

- **The image is built and published** by `build-images.yml` alongside the
  other six (`build-openhands` / `build-openhands-publish` / `retag-openhands`,
  signed and verified like the rest). It has no apko base of its own: the
  Dockerfile takes `ARG BASE_IMAGE` and builds `FROM` the sandbox base, so
  `build-sandbox-base` is also gated on the `openhands` paths filter.
- **Both endpoints resolve with no hand configuration.** `docker/compose.yml`
  and the CLI template set `SYNTHORG_PROVIDERS_GATEWAY_BASE_URL` and
  `SYNTHORG_TOOLS_CREDENTIALED_MCP_BASE_URL` from the published backend port
  and API prefix. The registered defaults stay empty, so a deployment that
  publishes no such address still fails closed rather than guessing.
- **The container can reach them.** It runs on the default bridge, not the
  compose network, so no service name resolves inside it; the wiring gives it
  a `host.docker.internal:host-gateway` alias. Because an egress-pinned
  container joins the sidecar's network namespace (and reads *its*
  `/etc/hosts`, Docker refusing `ExtraHosts` on the joining container), the
  alias is applied to the sidecar, and to the container itself only when no
  sidecar is involved.
- **The CLI treats it like the sidecar**: verified, digest-pinned, pulled,
  updated and pruned whenever the sandbox is enabled, which is also the
  precondition for the Docker socket the backend spawns it through.
- **The run cannot outlive its own credential.** `tools.openhands_max_runtime_seconds`
  caps the whole stream by wall clock, not just per-event idleness, because the
  idle deadline resets on every event and a steadily active run would otherwise
  keep going past the expiry of the bearer it authenticates with. The wiring
  fails the loop closed when that cap is not below
  `providers.gateway_token_ttl_seconds`, so the invariant is a precondition for
  wiring rather than a mid-run surprise.

Because the entrypoint is baked into the image, a change under
`docker/openhands/` reaches a deployment only through a rebuild. That matters
most for the [A/B recorder](loop-ab-harness.md), whose image setting defaults to
a published tag: a local run without `--openhands-image` measures the previously
published entrypoint and reports it as a result.

## Selection

The loop is chosen per task-complexity through the existing loop-selection
path, with `"openhands"` registered in the loop registry and both
known/buildable frozensets. **Availability is not routing.** Selection is off
by default: the boot wiring builds an `AutoLoopConfig` only when
`engine.loop_auto_select_enabled` is set, from the default complexity rules
merged with `engine.loop_complexity_overrides` and the
`engine.default_loop_type` fallback, and no default rule names `openhands`. An
operator routes a complexity band (or every unmatched task) to it explicitly;
which band, if any, should route there by default is the promotion decision
the A/B harness exists to answer. The registry factory requires
`OpenHandsLoopDeps`; without them it fails loud, so an unwired deployment can
never silently fall back to a different loop.

The scored comparison against the native loops, and the promotion
recommendation it feeds into those settings, is the
[inner-loop A/B harness](loop-ab-harness.md). An unwired OpenHands runtime
surfaces there as an explicitly unavailable row rather than a missing one.

## Proving the container protocol

`tests/integration/engine/test_openhands_container_contract.py` runs the built
image against a local OpenAI-compatible stub (plus the minimal MCP endpoint the
agent needs to build at all), with zero provider spend. It serialises the spec
with the production `_spec_line` and parses every stdout line with the
production `_parse_event`, so image/adapter drift fails there rather than in a
live run, and it asserts the spec parse, the event stream, the cost-delta and
token-delta arithmetic, termination, and the bearer scrub. It runs inside the
`build-openhands` job because that is the only place the freshly built image is
reachable on a pull request.

Asserting that every adapter-relevant kind appears is also the drift detector
for `_normalize`, which maps SDK events by `isinstance` against four classes: a
rename upstream shows up as a missing kind rather than a line on stderr nobody
reads. The env-gated live smoke stays the end-to-end check against a real
gateway and a real model.
