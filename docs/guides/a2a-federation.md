---
title: A2A Federation
description: Register a peer SynthOrg deployment, allowlist it, and drive the JSON-RPC gateway.
---

# A2A Federation

The Agent-to-Agent (A2A) bridge lets one SynthOrg deployment delegate tasks to a peer over JSON-RPC 2.0. The gateway is implemented and covered by in-process tests. No harness stands two deployments up against each other, so federation between live installs is unexercised; treat this guide as a description of the wire contract rather than a trodden path.

The gateway ships off (`a2a.enabled` defaults to `false`), and with an empty peer allowlist it accepts nothing even when switched on.

## Concepts

- **Peer**: another deployment, identified by name. A peer is admitted only when its name is in `a2a.allowed_peers` **and** the connection catalog holds a credential record for it, both of which the operator writes.
- **Method**: a JSON-RPC operation the gateway serves. The set is `message/send`, `tasks/get`, `tasks/cancel`, `skills/query`, and `skills/negotiate`.
- **Agent Card**: the safe-subset projection of an identity, served unauthenticated at `/.well-known/agent-card.json` (company level) and `/.well-known/agents/{agent_id}/agent-card.json`. It carries no budget, authority, or model configuration.
- **Envelope precedence**: the JSON-RPC `method` field on the envelope always wins. `parse_rpc_params` copies it over the `params` mapping before validating against a discriminated union keyed on it, so a caller who declares `tasks/get` on the envelope and smuggles `method: "message/send"` into `params` is validated as `tasks/get` and rejected on shape. Callers omit `method` from `params`; the gateway supplies it.

## Configuration surface

The gateway reads `A2AConfig` (`src/synthorg/a2a/config.py`) from the company YAML under the `a2a` key. There is no `peer_url` and no `peer_jwt_secret`: an outbound peer is a registered `a2a_peer` connection in the connection catalog, which is where its endpoint and credential live.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `a2a.enabled` | bool | `false` | Master switch. When off, no routes mount. |
| `a2a.allowed_peers` | list[str] | `()` | Inbound peer-name allowlist. Empty admits nobody, even when enabled. |
| `a2a.rate_limit_per_peer_rpm` | int | `100` | Requests per minute per `a2a_peer` connection. |
| `a2a.sse_idle_timeout_seconds` | int | `300` | Idle timeout before an SSE stream disconnects. |
| `a2a.max_request_body_bytes` | int | `1048576` | Inbound JSON-RPC body ceiling, enforced on `Content-Length` and again while streaming. |
| `a2a.agent_card_cache_ttl_seconds` | int | `60` | Agent Card cache TTL; `0` disables caching. |
| `a2a.auth.inbound_scheme` | enum | `api_key` | Default inbound scheme (`api_key`, `oauth2`, `bearer`, `mtls`, `none`). |
| `a2a.auth.outbound_scheme` | enum | `bearer` | Default outbound scheme. |
| `a2a.push.enabled` | bool | `false` | Accept push notifications at the unified webhook receiver. |
| `a2a.agent_card_verification.enabled` | bool | `false` | Verify Agent Card signatures against configured JWKS or PEM trust sources. |

Four keys are additionally runtime-editable through the `a2a` settings namespace (`src/synthorg/settings/definitions/a2a.py`), so a write applies without a restart: `a2a.client_timeout_seconds` (default `30.0`), `a2a.push_verification_clock_skew_seconds` (`300`), `a2a.card_cache_max_entries` (`512`), and `a2a.max_message_parts` (`100`).

## Authentication

Every inbound request clears three gates in order, and each answers with a JSON-RPC error envelope rather than a bare HTTP status:

1. **Peer identification.** The caller names itself in an `X-A2A-Peer-Name` header. Absent, the gateway answers `401` with JSON-RPC code `-32003`.
2. **Allowlist.** The name must appear in `a2a.allowed_peers`. Otherwise `403` with code `-32004`.
3. **Credentials.** The peer's stored record is fetched from the connection catalog and compared against the request's `X-API-Key` or `Authorization: Bearer` value, under the scheme the record declares. A mismatch, a missing header, a blank stored value, or an allowlisted peer with no credential record all fail closed with `401` and code `-32003`. The single fail-open branch is a deployment with no connection catalog wired at all, which is a development-only shape where the allowlist is the sole gate.

## Worked example: call a peer

The example uses two local processes on ports `8000` (node A, caller) and `8001` (node B, callee).

### Node B (callee)

```yaml
# /tmp/synthorg-b/config.yaml
a2a:
  enabled: true
  allowed_peers:
    - synthorg-a
```

Register node A as an `a2a_peer` connection on node B and store the API key it will present. The gateway reads the credential from the catalog; nothing in the YAML holds it.

```bash
SYNTHORG_DATA_DIR=/tmp/synthorg-b \
  SYNTHORG_BACKEND_PORT=8001 \
  uv run python -m synthorg.api
```

### Node A (caller)

```python
import httpx
import uuid

payload = {
    "jsonrpc": "2.0",
    "id": str(uuid.uuid4()),
    "method": "tasks/get",
    "params": {"id": "123e4567-e89b-12d3-a456-426614174000"},
}
resp = httpx.post(
    "http://localhost:8001/api/v1/a2a/",
    json=payload,
    headers={
        "Content-Type": "application/json",
        "X-A2A-Peer-Name": "synthorg-a",
        "X-API-Key": "<the key stored on node B>",
    },
)
print(resp.json())
```

Expected outcomes:

| Condition | HTTP | JSON-RPC code |
|---|---|---|
| Task exists and belongs to the calling peer | `200` | (a `result` block) |
| Unknown task | `404` | `-32001` |
| Missing `X-A2A-Peer-Name`, or credentials that do not match | `401` | `-32003` |
| Peer not on the allowlist | `403` | `-32004` |
| Method outside the supported set | `200` | `-32601` |
| Malformed `params` for the named method | `200` | `-32602` |
| Body over `max_request_body_bytes` | `413` | `-32006` |
| `Content-Type` other than `application/json` | `415` | `-32700` |

`tasks/get` and `tasks/cancel` additionally enforce per-peer ownership: `message/send` stamps the originating peer onto the task it creates, and a later read or cancel from a different peer is rejected.

## Observability

Every inbound JSON-RPC call emits these events (constants in `synthorg.observability.events.a2a`):

- `a2a.inbound.received`: at envelope decode; carries `method` and `request_id`.
- `a2a.inbound.auth_failed`: at any of the three authentication gates; carries the peer name and the reason.
- `a2a.jsonrpc.parse_error`: the body is not a valid JSON-RPC envelope.
- `a2a.jsonrpc.method_not_found`: the envelope named a method outside the supported set.
- `a2a.task.method_rejected`: a task method refused on ownership or lifecycle grounds.
- `api.boundary.validation_failed`: `parse_rpc_params` rejected a malformed `params` block.

## Threat model and extension

The `parse_rpc_params` boundary is the only validation gate; downstream handlers treat their typed `params` as already validated. The gateway holds the request body to `max_request_body_bytes` before parsing anything, checking the declared `Content-Length` first and then enforcing the same ceiling incrementally while streaming, so a lying header buys nothing.

To add a method:

1. Define an `A2A<Method>Params` model in `src/synthorg/a2a/rpc_params.py` with a `method` `Literal` discriminator.
2. Add it to the `A2ARpcParams` discriminated union.
3. Add the method name to `_SUPPORTED_METHODS` in `src/synthorg/api/a2a/gateway.py` and give it a `case` arm in `_dispatch_method`, which matches exhaustively over the union.
4. Cover the wire shape in `tests/unit/a2a/`.

See [typed boundaries](../reference/typed-boundaries.md) for the boundary contract and [the A2A protocol design](../design/a2a-protocol.md) for the full protocol.
