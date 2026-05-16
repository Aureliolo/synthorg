---
title: Webhook Management
description: Register inbound webhook receivers, choose the right envelope shape, observe retries and idempotency.
---

# Webhook Management

SynthOrg accepts inbound webhooks from external providers (GitHub, Stripe, Linear, etc.) at `/webhooks/{connection}`. Each receiver registers a connection record (transport, signing secret, replay window) and is handled by an integration-specific dispatcher that validates the typed envelope and routes the payload.

## Envelope contract

Wire shape: any JSON object. Inbound bodies route through `parse_typed("webhook.payload", body, WebhookEventPayload)` which enforces:

- Object root (arrays, scalars, non-JSON bodies are rejected with HTTP 400).
- Arbitrary keys via `ConfigDict(extra="allow")` so provider-specific schemas flow through unchanged.

Details: [docs/reference/typed-boundaries.md](../reference/typed-boundaries.md) (webhook payload envelope section).

## Configuration surface

| Key | Type | Default | Purpose |
|---|---|---|---|
| `integrations.webhooks.enabled` | bool | `false` | Master switch. |
| `integrations.webhooks.replay_window_seconds` | int | `300` | Reject nonces older than this. |
| `integrations.webhooks.max_payload_bytes` | int | `1048576` | Bound inbound body size. |
| `integrations.webhooks.idempotency_ttl_seconds` | int | `86400` | Idempotency-key cache lifetime. |

Connection records are stored per integration via `WebhookConnectionRepository`. Each connection carries `signing_secret`, `nonce_header`, `signature_header`, and a per-integration retry policy.

## Worked example: register and POST

Register a receiver for a `github` connection:

```python
from synthorg.integrations.webhooks.activity_service import WebhookActivityService

service = WebhookActivityService(...)
await service.register(
    connection_type="github",
    connection_name="primary",
    signing_secret="whsec_PROVIDED_BY_GITHUB",
    nonce_header="X-GitHub-Delivery",
    signature_header="X-Hub-Signature-256",
)
```

POST a sample payload from the command line:

```bash
NONCE=$(uuidgen)
TS=$(date +%s)
BODY='{"action":"opened","number":7,"pull_request":{"id":42}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac whsec_PROVIDED_BY_GITHUB | sed 's/^.* //')

curl -i http://localhost:8000/webhooks/github/primary \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: $NONCE" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  --data "$BODY"
```

Expected:

- `204 No Content` on the first delivery (handler accepted).
- `204` again on a retry within the replay window (idempotency-key short-circuit).
- `400` on a malformed JSON body (envelope rejection at `parse_typed` time).
- `401` on a signature mismatch.
- `409` on a replay older than the replay window.

## Retry semantics

Providers retry on non-2xx responses; SynthOrg accepts duplicate deliveries up to `idempotency_ttl_seconds`. The composed idempotency key is `connection_name:event_type:nonce`, length-clamped to `255` chars (DB schema cap), then folded to a SHA-256 digest if oversized so the cache lookup never fails on length.

Per-delivery state transitions land on the `WebhookReceipt`:

- `received` -> `dispatched` (handler returned) -> `acknowledged` (downstream completed).
- `received` -> `duplicate` when the idempotency key has been observed.
- `received` -> `rejected` on signature/nonce failure.

The `WEBHOOK_RECEIPT_STATUS_TRANSITIONED` event fires AFTER each persistence write so dashboards can chart delivery health.

## Adding a new provider

1. Add a row to `WebhookConnectionRepository` schema (already covers the common columns).
2. Implement a handler in `src/synthorg/integrations/webhooks/handlers/<provider>.py` that consumes a typed `WebhookEventPayload` and dispatches to the right service.
3. Register the handler in the dispatcher's strategy registry.
4. Add a per-provider test under `tests/unit/integrations/webhooks/` covering accept, replay, signature mismatch, and oversized payload.

See [docs/design/integrations.md](../design/integrations.md) for the broader integrations architecture.
