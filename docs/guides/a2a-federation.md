---
title: A2A Federation
description: Register a peer SynthOrg deployment, expose JSON-RPC methods, route tasks across the federation.
---

# A2A Federation

The Agent-to-Agent (A2A) bridge lets one SynthOrg deployment delegate tasks to a peer over JSON-RPC. Each side authenticates with a shared JWT credential and the typed boundary at `synthorg.a2a.rpc_params.parse_rpc_params` validates every inbound `params` block. This guide walks through registering a peer, enabling specific RPC methods, and observing a federation round-trip.

## Concepts

- **Peer**: a SynthOrg deployment reachable at an HTTPS URL with a JSON-RPC endpoint mounted at `/a2a`.
- **Method**: a JSON-RPC operation the gateway exposes. The current method set is `message/send`, `tasks/get`, and `tasks/cancel`.
- **Envelope precedence**: the JSON-RPC `method` field on the envelope always wins; a `method` key smuggled inside `params` is rejected at `parse_rpc_params` time.

## Configuration surface

Settings live under the `a2a` namespace. Resolve them via `SettingsService` or set them in the company-template YAML.

| Key | Type | Default | Purpose |
|---|---|---|---|
| `a2a.enabled` | bool | `false` | Master switch for the federation gateway. |
| `a2a.peer_url` | URL | (unset) | Outbound peer endpoint. |
| `a2a.peer_jwt_secret` | secret | (unset) | HMAC key for outbound JWT. |
| `a2a.methods_enabled` | list[str] | `[]` | Allowlist of inbound methods. |
| `a2a.timeout_seconds` | float | `30` | Per-request wall-clock budget. |

## Worked example: two-node round-trip

The example uses two local processes on ports `8000` (node A) and `8001` (node B); each side has the other registered as its peer.

### Node B (callee)

```bash
SYNTHORG_DATA_DIR=/tmp/synthorg-b \
  SYNTHORG_BACKEND_PORT=8001 \
  uv run python -m synthorg.api
```

```yaml
# /tmp/synthorg-b/config.yaml
a2a:
  enabled: true
  methods_enabled:
    - tasks/get
  peer_jwt_secret: "shared-secret-do-not-commit"
```

### Node A (caller)

```yaml
# /tmp/synthorg-a/config.yaml
a2a:
  enabled: true
  peer_url: http://localhost:8001/a2a
  peer_jwt_secret: "shared-secret-do-not-commit"
  methods_enabled: []
```

Call `tasks/get` from node A:

```python
import httpx
import jwt
import uuid

token = jwt.encode({"sub": "synthorg-a", "aud": "synthorg-b"}, "shared-secret-do-not-commit", algorithm="HS256")

payload = {
    "jsonrpc": "2.0",
    "id": str(uuid.uuid4()),
    "method": "tasks/get",
    "params": {"task_id": "task-12345"},
}
resp = httpx.post(
    "http://localhost:8001/a2a",
    json=payload,
    headers={"Authorization": f"Bearer {token}"},
)
print(resp.json())
```

Expected outcomes:

- `200` with a `result` block when the task exists.
- `404` (mapped to JSON-RPC error `-32602` with `data.code: "task_not_found"`) when the task is unknown.
- `403` when the bearer JWT does not validate (peer secret mismatch or `aud` claim incorrect).

## Observability

Every inbound JSON-RPC call emits these events:

- `a2a.jsonrpc.received`: at envelope decode; carries `peer`, `method`, `id`.
- `api.boundary.validation_failed`: when `parse_rpc_params` rejects a malformed `params` block.
- `a2a.jsonrpc.dispatched`: at successful method dispatch.
- `a2a.jsonrpc.error`: at error path (with `code` and `message`).

The `a2a.dispatch_latency_seconds` histogram has a `method` label so per-RPC latency is easy to chart.

## Threat model + extension

The boundary check is the only validation gate; downstream handlers MUST treat their typed `params` as already-validated.

To add a new method:

1. Define an `A2A<Method>Params` Pydantic model under `src/synthorg/a2a/rpc_params.py`.
2. Add it to the `A2ARpcParams` discriminated union.
3. Register the handler in the gateway registry.
4. Add the method name to the per-peer `a2a.methods_enabled` allowlist.
5. Cover the wire shape in `tests/unit/a2a/test_<method>.py`.

See [docs/reference/typed-boundaries.md](../reference/typed-boundaries.md) for the boundary contract and [docs/design/a2a-protocol.md](../design/a2a-protocol.md) for the full protocol design.
