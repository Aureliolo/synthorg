---
title: REST API Examples
description: Authenticate and call the 10 most common SynthOrg REST endpoints via curl, Python (httpx), and JavaScript (fetch).
---

# REST API Examples

The SynthOrg REST API is mounted at `/api/v1` on the backend service (default port `3001`). Every endpoint requires a Bearer JWT token; the response is a typed envelope (`ApiResponse<T>` or `PaginatedResponse<T>`). This guide shows the 10 most common operations.

The base URL placeholder `$BASE` defaults to `http://localhost:3001`.

## Authenticate

### curl

```bash
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  --data '{"username":"admin","password":"admin"}' \
  | jq -r '.data.token')
```

### Python (httpx)

```python
import httpx

resp = httpx.post(
    "http://localhost:3001/api/v1/auth/login",
    json={"username": "admin", "password": "admin"},
)
resp.raise_for_status()
token = resp.json()["data"]["token"]
```

### JavaScript (fetch)

```javascript
const resp = await fetch('http://localhost:3001/api/v1/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin' }),
})
const token = (await resp.json()).data.token
```

## 1. List agents

```bash
curl -s "$BASE/api/v1/agents" -H "Authorization: Bearer $TOKEN" | jq
```

```python
resp = httpx.get(f"{base}/api/v1/agents", headers={"Authorization": f"Bearer {token}"})
agents = resp.json()["data"]
```

```javascript
const r = await fetch(`${base}/api/v1/agents`, { headers: { Authorization: `Bearer ${token}` } })
const { data: agents } = await r.json()
```

Returns a paginated envelope; the `meta.next_cursor` field drives the next page.

## 2. Create a task

```bash
curl -s -X POST "$BASE/api/v1/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"title":"Build a sample","description":"Smoke test","acceptance_criteria":["Compiles","Runs"]}'
```

```python
resp = httpx.post(
    f"{base}/api/v1/tasks",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "title": "Build a sample",
        "description": "Smoke test",
        "acceptance_criteria": ["Compiles", "Runs"],
    },
)
task = resp.json()["data"]
```

```javascript
const r = await fetch(`${base}/api/v1/tasks`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ title: 'Build a sample', description: 'Smoke test', acceptance_criteria: ['Compiles', 'Runs'] }),
})
const { data: task } = await r.json()
```

## 3. Get a task

```bash
curl -s "$BASE/api/v1/tasks/$TASK_ID" -H "Authorization: Bearer $TOKEN" | jq
```

```python
resp = httpx.get(f"{base}/api/v1/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
task = resp.json()["data"]
```

## 4. List artifacts for a task

```bash
curl -s "$BASE/api/v1/artifacts?task_id=$TASK_ID" -H "Authorization: Bearer $TOKEN" | jq
```

```python
resp = httpx.get(
    f"{base}/api/v1/artifacts",
    params={"task_id": task_id},
    headers={"Authorization": f"Bearer {token}"},
)
artifacts = resp.json()["data"]
```

## 5. Submit a client request

```bash
curl -s -X POST "$BASE/api/v1/requests" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"client_id":"c-1","requirement":{"title":"Ship the thing","description":"Make it work","acceptance_criteria":["Tests pass"]}}'
```

```python
resp = httpx.post(
    f"{base}/api/v1/requests",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "client_id": "c-1",
        "requirement": {
            "title": "Ship the thing",
            "description": "Make it work",
            "acceptance_criteria": ["Tests pass"],
        },
    },
)
```

## 6. Approve a client request

```bash
curl -s -X POST "$BASE/api/v1/requests/$REQUEST_ID/approve" \
  -H "Authorization: Bearer $TOKEN"
```

The approve endpoint walks the request through the intake engine (when in `SUBMITTED` status) or finalises a previously-scoped request.

## 7. Fetch budget utilisation

```bash
curl -s "$BASE/api/v1/budget/utilization" -H "Authorization: Bearer $TOKEN" | jq
```

```python
resp = httpx.get(f"{base}/api/v1/budget/utilization", headers={"Authorization": f"Bearer {token}"})
util = resp.json()["data"]
print(f"Monthly: {util['monthly_used_percent']:.1f}% Daily: {util['daily_used_percent']:.1f}%")
```

## 8. Decide on a pending approval

```bash
curl -s -X POST "$BASE/api/v1/approvals/$APPROVAL_ID/decide" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"verdict":"approve","rationale":"Canary signal clean."}'
```

```python
resp = httpx.post(
    f"{base}/api/v1/approvals/{approval_id}/decide",
    headers={"Authorization": f"Bearer {token}"},
    json={"verdict": "approve", "rationale": "Canary signal clean."},
)
```

## 9. Invoke an MCP tool

```bash
curl -s -X POST "$BASE/api/v1/mcp/invoke" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"tool":"hello.greet","arguments":{"name":"world","times":2}}'
```

```javascript
const r = await fetch(`${base}/api/v1/mcp/invoke`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ tool: 'hello.greet', arguments: { name: 'world', times: 2 } }),
})
const result = await r.json()
```

## 10. Subscribe to the live event WebSocket

```javascript
const ws = new WebSocket(`ws://localhost:3001/api/v1/ws?token=${token}`)
ws.onmessage = (e) => {
  const evt = JSON.parse(e.data)
  console.log('[event]', evt.event_type, evt.payload)
}
ws.onopen = () => {
  ws.send(JSON.stringify({ action: 'subscribe', channels: ['tasks', 'approvals'] }))
}
```

The first frame the server sends is `{"event_type":"auth_ok"}`; once seen, the channels you subscribed to deliver events in real time. See [docs/reference/websocket-protocol.md](../reference/websocket-protocol.md) for the full handshake and event-type catalogue.

## Pagination

List endpoints return `PaginatedResponse<T>`:

```json
{
  "data": [...],
  "meta": {
    "limit": 50,
    "next_cursor": "eyJsYXN0X2lkIjoidGFzay0xMjMifQ==",
    "has_more": true
  }
}
```

To fetch the next page: pass `?cursor=<value>` to the same endpoint. Stop when `has_more` is false.

## Error envelopes

Errors follow RFC 9457:

```json
{
  "type": "synthorg/not-found",
  "title": "Task not found",
  "status": 404,
  "detail": "Task 'task-X' not found",
  "code": "RESOURCE_NOT_FOUND",
  "category": "client_error"
}
```

The `code` field is the typed `ErrorCode` enum (see [docs/reference/errors.md](../reference/errors.md)). Clients can switch on the enum without parsing prose.
