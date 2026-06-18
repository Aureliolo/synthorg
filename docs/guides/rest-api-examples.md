---
title: REST API Examples
description: Authenticate and call the 10 most common SynthOrg REST endpoints via curl, Python (httpx), and JavaScript (fetch).
---

# REST API Examples

The SynthOrg REST API is mounted at `/api/v1` on the backend service (default port `3001`). Every endpoint requires authentication; the JWT is delivered as an HttpOnly `Set-Cookie` header by `/auth/login`, so subsequent calls authenticate by carrying the cookie back, not by attaching an `Authorization: Bearer` header. The response envelope is a typed `ApiResponse<T>` or `PaginatedResponse<T>`. This guide shows the 10 most common operations.

The base URL placeholder `$BASE` defaults to `http://localhost:3001`. Examples assume `jq` is installed for response inspection.

## Authenticate

### curl

```bash
# Login. -c writes the session cookie to a jar; -b on every subsequent
# call reads it back. The response body carries only metadata
# (expires_in, must_change_password); the JWT is in Set-Cookie.
curl -s -c cookies.txt -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  --data '{"username":"admin","password":"admin"}' | jq
```

### Python (httpx)

```python
import httpx

# httpx.Client persists cookies on its ``.cookies`` jar between calls.
client = httpx.Client(base_url="http://localhost:3001")
resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
resp.raise_for_status()
# Token is in client.cookies now; every subsequent client.get/post
# carries it back automatically.
```

### JavaScript (fetch)

```javascript
// credentials: 'include' both sends and accepts cookies. In a browser
// this works against same-origin or CORS-allowed targets; in Node 18+
// fetch use undici's cookie jar via dispatchers (see node docs).
const resp = await fetch('http://localhost:3001/api/v1/auth/login', {
  method: 'POST',
  credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: 'admin' }),
})
const { data: session } = await resp.json()
console.log('session expires in', session.expires_in, 'seconds')
```

## 1. List agents

```bash
curl -s -b cookies.txt "$BASE/api/v1/agents" | jq
```

```python
agents = client.get("/api/v1/agents").json()["data"]
```

```javascript
const r = await fetch(`${base}/api/v1/agents`, { credentials: 'include' })
const { data: agents } = await r.json()
```

Returns a paginated envelope; the `meta.next_cursor` field drives the next page.

## 2. Create a task

```bash
curl -s -b cookies.txt -X POST "$BASE/api/v1/tasks" \
  -H "Content-Type: application/json" \
  --data '{"title":"Build a sample","description":"Smoke test","type":"development","project":"'"$PROJECT_ID"'","created_by":"'"$AGENT_NAME"'"}'
```

```python
resp = client.post(
    "/api/v1/tasks",
    json={
        "title": "Build a sample",
        "description": "Smoke test",
        "type": "development",
        "project": project_id,
        "created_by": agent_name,
    },
)
task = resp.json()["data"]
```

```javascript
const r = await fetch(`${base}/api/v1/tasks`, {
  method: 'POST',
  credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ title: 'Build a sample', description: 'Smoke test', type: 'development', project: projectId, created_by: agentName }),
})
const { data: task } = await r.json()
```

## 3. Get a task

```bash
curl -s -b cookies.txt "$BASE/api/v1/tasks/$TASK_ID" | jq
```

```python
task = client.get(f"/api/v1/tasks/{task_id}").json()["data"]
```

## 4. List artifacts for a task

```bash
curl -s -b cookies.txt "$BASE/api/v1/artifacts?task_id=$TASK_ID" | jq
```

```python
resp = client.get("/api/v1/artifacts", params={"task_id": task_id})
artifacts = resp.json()["data"]
```

## 5. Submit a client request

```bash
curl -s -b cookies.txt -X POST "$BASE/api/v1/requests" \
  -H "Content-Type: application/json" \
  --data '{"client_id":"c-1","requirement":{"title":"Ship the thing","description":"Make it work","acceptance_criteria":["Tests pass"]}}'
```

```python
resp = client.post(
    "/api/v1/requests",
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
curl -s -b cookies.txt -X POST "$BASE/api/v1/requests/$REQUEST_ID/approve"
```

The approve endpoint walks the request through the intake engine (when in `SUBMITTED` status) or finalises a previously-scoped request.

## 7. Fetch budget utilisation

```bash
curl -s -b cookies.txt "$BASE/api/v1/analytics/overview" | jq
```

```python
overview = client.get("/api/v1/analytics/overview").json()["data"]
print(f"Budget used: {overview['budget_used_percent']:.1f}%")
```

## 8. Approve a pending approval

Approvals are decided through dedicated `/approve` and `/reject` endpoints (there is
no combined `/decide` route). Both accept an optional `comment`.

```bash
curl -s -b cookies.txt -X POST "$BASE/api/v1/approvals/$APPROVAL_ID/approve" \
  -H "Content-Type: application/json" \
  --data '{"comment":"Canary signal clean."}'
```

```python
resp = client.post(
    f"/api/v1/approvals/{approval_id}/approve",
    json={"comment": "Canary signal clean."},
)
# To reject instead:
# client.post(f"/api/v1/approvals/{approval_id}/reject", json={"comment": "Needs rework."})
```

## 9. Subscribe to the live event WebSocket

The WebSocket uses a two-step ticket handshake: exchange your session for a one-time
ticket, then send it as the first frame after the socket opens.

```javascript
// 1. Exchange the session cookie for a one-time WebSocket ticket.
const ticketResp = await fetch(`${base}/api/v1/auth/ws-ticket`, {
  method: 'POST',
  credentials: 'include',
})
const { data: { ticket } } = await ticketResp.json()

// 2. Open the socket and authenticate with the ticket on the first frame.
const ws = new WebSocket(`ws://localhost:3001/api/v1/ws`)
ws.onopen = () => {
  ws.send(JSON.stringify({ action: 'auth', ticket }))
}
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data)
  if (msg.action === 'auth_ok') {
    // 3. Once authenticated, subscribe to channels.
    ws.send(JSON.stringify({ action: 'subscribe', channels: ['tasks', 'approvals'] }))
    return
  }
  console.log('[event]', msg.event_type, msg.payload)
}
```

The server's auth acknowledgement frame is `{"action":"auth_ok"}`; once seen, the
channels you subscribed to deliver events in real time. See the
[WebSocket Models](../api/layer.md#websocket-models) section of the API reference for
the full handshake and event-type catalogue.

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
  "detail": "Task '123e4567-e89b-12d3-a456-426614174000' not found",
  "code": "RESOURCE_NOT_FOUND",
  "category": "client_error"
}
```

The `code` field is the typed `ErrorCode` enum (see [docs/reference/errors.md](../reference/errors.md)). Clients can switch on the enum without parsing prose.
