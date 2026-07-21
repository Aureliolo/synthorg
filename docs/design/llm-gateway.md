# LLM Gateway

The LLM gateway is an OpenAI-compatible HTTP surface that fronts the
in-process provider registry. It exists so an embedded coding harness
(OpenHands, the fourth [execution loop](openhands-loop.md)) can point its
LiteLLM client at a `base_url` and still route every call through
SynthOrg's governance: explicit `(provider, model)` binding, per-run cost
and prompt attribution, a hard token-budget kill, and SEC-1 log
redaction. Provider-agnosticism becomes a **gateway property**, not a
harness property.

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

## Per-run signed bearer

The gateway mints one short-lived HMAC-SHA256 token per agent run
(`llm/gateway_token.py`) and hands it to the harness as its OpenAI
`api_key`. The token binds `(execution_id, agent_id, task_id, project_id,
provider, model_id, cost_ceiling, currency)` and an expiry, so the gateway
can enforce Explicit Provider Binding and the budget from the request
alone, with **no server-side session table**. The signer is shared
in-process: the boot wiring hands the [OpenHands loop](openhands-loop.md)
the *same* `GatewaySigner` instance the gateway verifies with (pulled from
the gateway feature slice), so a token minted by the loop is accepted by
the gateway without a second secret to keep in sync.

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
   prompt purpose (the embedded harness issues arbitrary prompts), so
   `purpose` is `None`: cost is attributed by run and call-category through
   the single provider chokepoint, not by prompt purpose. The response is
   translated back to the
   OpenAI shape with `usage` echoed for the adapter's `TurnRecord`s.
5. **SEC-1 posture**: fencing of untrusted upstream content is enforced at
   the source (the [credentialed-MCP](credentialed-mcp.md) boundary wraps
   tool outputs). At the gateway we route every log through
   `safe_error_description` / `scrub_secret_tokens`, run the shared
   injection heuristics over inbound content as an advisory signal, and set
   OTLP spans with `record_exception=False`.

Streaming uses `text/event-stream`; setup errors (token/binding/budget)
are surfaced as HTTP status codes by eagerly fetching the first frame,
never as a half-open stream.

## Settings

All under the `providers` namespace, off by default, hot-reloadable:
`gateway_enabled`, `gateway_token_ttl_seconds`, `gateway_base_url` (the
sandbox-reachable app address handed to the harness). Enabling the gateway
opens an egress path, so the `providers.gateway_enabled` `false -> true`
transition routes through the deliberate confirm+reason+actor guardrail in
`settings/write_governance.py`.

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
