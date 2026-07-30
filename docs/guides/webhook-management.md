---
title: Webhook Management
description: Give a connection a signing secret, POST a verified delivery, and understand what ingest accepts and refuses.
---

# Webhook Management

SynthOrg accepts inbound webhooks at `/webhooks/{connection_name}/{event_type}`.
There is no standalone webhook registration: ingest authenticates a delivery
against the `signing_secret` credential on an existing connection, and a
connection without one has no reachable inbound path.

Verified deliveries are published to the `#webhooks` channel on the message bus,
where `ExternalTriggerStrategy` matches the event name against configured
ceremony triggers. The payload itself is not forwarded into any prompt.

## Which connection types can receive one

Only the types with a registered verifier: `github`, `gitlab`, `gitea`,
`forgejo`, `slack`, `generic_http`, `a2a_peer`. Each exposes a `signing_secret`
credential field, optional on all but `slack`. `generic_http` exposes it only for
a custom vendor, because a vendor preset describes an API you call rather than
one you hear from.

`GET /api/v1/connections/types` reports this per type as `webhook_secret_field`:
the field name when ingest is reachable, `null` when it can never be. Read it
rather than keeping a list, so a client cannot drift from the verifier coverage.

The full verifier table (algorithms, signature headers, delivery-id headers) is
in [docs/design/integrations.md](../design/integrations.md).

## Envelope contract

Any JSON object. Inbound bodies route through
`parse_typed("webhook.payload", body, WebhookEventPayload)`, which requires an
object root (arrays, scalars and non-JSON are rejected 400) and allows arbitrary
keys so provider schemas flow through unchanged. Details:
[docs/reference/typed-boundaries.md](../reference/typed-boundaries.md).

## Configuration surface

| Key | Type | Default | Purpose |
|---|---|---|---|
| `integrations.webhook_receipt_retention_days` | int | `0` | Receipt retention window in days; `0` never sweeps. |

The remaining knobs live on `RootConfig.integrations.webhooks` rather than the
settings registry: `replay_window_seconds` (300), `max_payload_bytes` (1000000)
and `rate_limit_rpm` (100). Signature verification is not configurable; it runs
on every delivery.

## Worked example: give a connection a secret, then POST

A signing secret is credential material, so it is captured **out of band** and
never sent in the create body. Sending it inline is refused at the boundary.

Capture the secret against a client-chosen draft id, then create the connection
with the returned handle:

```bash
DRAFT=$(uuidgen)

HANDLE=$(curl -s -b cookies.txt -X POST \
  "http://localhost:8000/api/v1/connections/drafts/$DRAFT/fields/signing_secret/capture" \
  -H "Content-Type: application/json" \
  --data '{"value": "a-secret-of-at-least-16-chars", "secret_kind": "signing_secret"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["handle"])')

curl -s -b cookies.txt -X POST http://localhost:8000/api/v1/connections \
  -H "Content-Type: application/json" \
  --data "{
    \"name\": \"primary\",
    \"connection_type\": \"github\",
    \"connection_draft_id\": \"$DRAFT\",
    \"credential_handles\": {\"signing_secret\": \"$HANDLE\"}
  }"
```

The secret must be at least 16 non-whitespace characters: it is compared against
a header on an endpoint reachable without credentials.

Then POST a signed delivery:

```bash
SECRET='a-secret-of-at-least-16-chars'
BODY='{"action":"opened","number":7,"pull_request":{"id":42}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')

curl -i http://localhost:8000/webhooks/primary/issues.opened \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: $(uuidgen)" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  --data "$BODY"
```

Note the path order: **connection name first, then event type**. The event type
is yours to choose and is what ceremony triggers match on.

Expected:

- `202 Accepted` on the first delivery.
- `202` again on a byte-identical retry, short-circuited by the idempotency cache.
- `409` on a replay of the same body once the first has been recorded.
- `400` on a malformed or non-object JSON body.
- `401` on anything that cannot be authenticated.

**Every pre-authentication failure answers the same 401** with one message,
whether the connection does not exist, its type has no verifier, its secret is
unset, or the signature did not match. The endpoint takes no credentials, so
distinguishing them would let an unauthenticated caller enumerate connections
and probe their configuration. The specific reason is in the structured log
(`integrations.webhook.rejected`).

## Deduplication

The idempotency key is derived from `sha256(body)`, never from a header. Header
ids sit outside every verifier's signature, so keying on one would let a single
captured signed body publish repeatedly under fresh values. A provider's own
retry of an identical body therefore collapses onto the first publish on any
replica; a genuinely new delivery has a different body and its own key.

The provider's delivery id (`X-GitHub-Delivery` and friends, declared per
verifier) is recorded for traceability only.

## Receipts

`WebhookReceipt` rows carry the connection, event type, status, timestamps and
the raw body, and `WebhookReceiptService` / `WebhookActivityService` read and
retry them.

**No code path writes one yet.** The repositories, the services and the retention
sweep are all wired, but nothing populates the table, so the activity endpoint
and the retention setting currently have nothing to act on.

## Adding a new provider

1. Implement a `SignatureVerifier` under
   `src/synthorg/integrations/webhooks/verifiers/`, exposing `signature_header`,
   `delivery_id_header`, and `verify(body, headers, secret)`. Compare digests
   with `hmac.compare_digest`, and return `False` (never raise) on a missing or
   malformed header.
2. Register it in `_VERIFIER_FACTORIES` in `verifiers/factory.py`.
3. Add a `signing_secret` credential field to that connection type in
   `integrations/connections/field_metadata.py`, via the `_signing_secret(...)`
   factory. Without it the verifier is unreachable, which
   `test_a_type_with_a_secret_field_has_a_verifier` and its converse both check.
4. Add tests covering accept, replay, signature mismatch, missing header, and
   oversized payload.

See [docs/design/integrations.md](../design/integrations.md) for the broader
integrations architecture.
