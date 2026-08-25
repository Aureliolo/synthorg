# LLM Gateway

The LLM gateway is an OpenAI-compatible HTTP surface that fronts the
in-process provider registry. It exists so a process outside the runtime can
point its client at a `base_url` and still route every call through
SynthOrg's governance: explicit `(provider, model)` binding, per-run cost
and prompt attribution, a hard token-budget kill, and SEC-1 log
redaction. Provider-agnosticism becomes a **gateway property**, not a
property of whatever dials it.

Nothing the product itself runs dispatches here: the agent loop reaches the
provider registry in-process. The gateway's live consumer is the recording
harness under `evals/`, which routes every completion of a measured run
through it precisely because that is the one place a run's real spend and its
prompts are both observable.

## Boundary

There is no network-facing gateway in the base system: `LiteLLMDriver`
calls `litellm.acompletion` in-process. The gateway mounts a controller
(`api/gateway/controller.py`) on the existing Litestar app at
`POST /gateway/v1/chat/completions`, so the full mounted route (under the
app's `/api/v1` prefix) is `/api/v1/gateway/v1/chat/completions`; the
harness's OpenAI `base_url` is the app address plus `/api/v1/gateway/v1`.
It is reachable **only** from the agent sandbox over the sidecar egress
allowlist, and is excluded from the session/bearer auth middleware
because it authenticates with its own per-run signed bearer.

That exclusion leaves `scope["user"]` unset, which the rate limiter would
otherwise read as anonymous: the tier sized for a stranger with an IP, which an
agent doing ordinary work spends in seconds before dying on a 429 from its own
control plane. `api/rate_limits/tiers.py::bears_own_credential` therefore puts
this route (and the credentialed-tool MCP server) on the authenticated tier,
but **only** when the request actually presents a well-formed
`Authorization: Bearer` header. The path alone says where a request was aimed,
not who sent it, and the authenticated tier is far larger. Syntax is all the
throttle can check: verifying the signature is the handler's job and doing it
twice would put the signing key in the rate-limit path, so a forged-but-
well-formed header still reaches the larger bucket. It stays keyed by client IP
there (no user is bound on these routes), and every request in it still fails
the handler's verification.

## Per-run signed bearer

The gateway mints one short-lived HMAC-SHA256 token per agent run
(`llm/gateway_token.py`) and hands it to the harness as its OpenAI
`api_key`. The token binds `(execution_id, agent_id, task_id, project_id,
provider, model_id, cost_ceiling, currency)` and an expiry, so the gateway
can enforce Explicit Provider Binding and the budget from the request
alone, with **no server-side session table**. The signer is shared
in-process: whatever mints a run token pulls the *same* `GatewaySigner`
instance the gateway verifies with, out of the gateway feature slice, so a
minted token is accepted without a second secret to keep in sync. A caller
holding an instance of its own gets a 401 on every request, which is the
defect the sharing exists to prevent.

Minting is the single enforcement point for Explicit Provider Binding
(`llm/gateway_binding.py`): a `ModelRef` with no bound provider raises
`GatewayModelUnboundError` rather than letting the gateway later auto-pick.

## Request pipeline (order is load-bearing)

1. **Verify bearer** into a claims object; reject expired/invalid
   (`GatewayTokenInvalidError`, 401). The raw token is never logged.
2. **Explicit Provider Binding**: dispatch is bound to the token's
   `(provider, model_id)`, resolved via `ProviderRegistry.get(provider)`,
   **never** the request's `model` field. A missing driver is a 503.
3. **Hard token budget / kill**: a per-run cost ledger
   (`api/gateway/ledger.py`) is checked pre-flight; once a run has spent
   its ceiling, further calls are refused (`GatewayBudgetExhaustedError`,
   402, which the adapter maps to `BUDGET_EXHAUSTED`). The check is
   boundary-based per call, matching the native loop's `run_hard_ceiling`
   semantics (an in-flight call finishes). The `providers.gateway_enabled`
   master toggle short-circuits to 503.
4. **Dispatch under cost scope**: `provider.complete` / `provider.stream`
   run inside `cost_recording_scope(purpose=None,
   call_category=PRODUCTIVE, ...)`. The gateway carries no single registered
   prompt purpose (a caller outside the runtime issues arbitrary prompts), so
   `purpose` is `None`: cost is attributed by run and call-category through
   the single provider chokepoint, not by prompt purpose. The response is
   translated back to the
   OpenAI shape with `usage` echoed for the adapter's `TurnRecord`s.
5. **SEC-1 posture**: fencing of untrusted upstream content is enforced at
   the source, where the tool that fetched it knows what it is. At the
   gateway we route every log through
   `safe_error_description` / `scrub_secret_tokens`, run the shared
   injection heuristics over inbound content as an advisory signal, and set
   OTLP spans with `record_exception=False`.

Streaming uses `text/event-stream`; setup errors (token/binding/budget)
are surfaced as HTTP status codes by eagerly fetching the first frame,
never as a half-open stream.

Both shapes carry the model's reasoning on `reasoning_content`, the streaming
path per delta and the buffered path on the message. It rides its own key
rather than `content` because it is the model's working and not its answer, so
a harness folds it into the transcript only if it chooses to; and it is on both
shapes because otherwise whether a client can see reasoning at all depends on
whether it streams, which is a decision about transport rather than about the
model.

Token counts follow the same rule. A buffered response always carries `usage`;
a stream carries it when the client sets `stream_options.include_usage`, as a
terminal chunk with an empty `choices` list immediately before `[DONE]`. It is
conditional because that is the OpenAI contract: a client that did not ask
expects every chunk to carry a choice. What the gateway never does is invent
the numbers. When a client asks and the provider stream reports no usage, the
gateway sends no usage chunk and logs `gateway.usage.unreported`, because zeros
would tell the harness the call was free. The gateway's own ledger is fed from
the same provider event, so a client's accounting and the run's cost ceiling
are reading one measurement rather than two.

## Settings

One, `providers.gateway_enabled`, re-read live per request so toggling it
takes effect on the next call.

A bearer's lifetime is not a setting. It has to outlive the longest run that
dispatches through the gateway, and the harness minting the bearer is the only
thing that knows how long that is, so it owns the value
(`_BEARER_TTL_SECONDS` in `evals/harness/binding.py`). An operator knob would
be a second answer to a question the caller has already settled, and a wrong
one fails a paid run part way through rather than at the ceiling that bounds
it.

`gateway_enabled` ships **off**. Nothing inside the product dispatches through
the gateway, so an enabled endpoint with no caller is surface for nothing; the
recording harness turns it on for the length of a run. The route carries no
ambient authority even when enabled, authenticating only with a per-run signed
bearer, but enabling it opens a path on which billed LLM calls can be made
from outside the runtime. The **first** stored `true` therefore routes through
the deliberate confirm+reason+actor guardrail in
`settings/write_governance.py`, unset counting as off, because for a
default-off capability the first enable is the transition that matters.

## Wiring

The gateway is an auto-discovered feature (`api/gateway/feature.py`): the
construction wirer builds the signer, the run-cost ledger and the request
pipeline unconditionally and commits them to `GatewayStateSlice`; the
controller mounts whenever the pipeline is wired and 503s while disabled.

## Reuse

`ProviderRegistry` / `CompletionProvider`, `cost_recording_scope`,
`resolve_ref_provider` (Explicit Provider Binding),
`wrap_untrusted` / `safe_error_description` / `scrub_secret_tokens`.

## Convention gate

`scripts/check_gateway_explicit_binding.py` enforces that the gateway
dispatch path binds `(provider, model)` from the token, never the request
`model`, and never auto-picks a provider.
