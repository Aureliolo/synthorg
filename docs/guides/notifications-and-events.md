---
title: Notifications & Event Subscriptions
description: Configure notification sinks (console, ntfy, Slack, email), subscribe to WebSocket event channels, and integrate with external alerting systems.
---

# Notifications & Event Subscriptions

SynthOrg emits two complementary streams of updates: **notifications** (operator-facing alerts fanned out through the `NotificationDispatcher` to sinks like Slack or ntfy) and **WebSocket events** (real-time UI updates pushed to the dashboard via channels). This guide shows how to configure each, subscribe external systems, and compose them into an alerting pipeline.

---

## Notification Sinks

Notifications are alerts that require operator attention: approval gate decisions, budget threshold breaches, timeout escalations, system errors. They fan out concurrently to every registered sink.

### Configuration

```yaml
notifications:
  min_severity: info          # info, warning, error, critical
  sinks:
    - type: console
      enabled: true
    - type: ntfy
      enabled: true
      params:
        server_url: "https://ntfy.example.com"
        topic: "synthorg-alerts"
        token: "${NTFY_TOKEN}"
    - type: slack
      enabled: true
      params:
        webhook_url: "${SLACK_WEBHOOK_URL}"
    - type: email
      enabled: false
      params:
        host: "smtp.example.com"
        port: "587"
        username: "${SMTP_USER}"
        password: "${SMTP_PASSWORD}"
        from_addr: "synthorg@example.com"
        to_addrs: "ops@example.com,oncall@example.com"
        use_tls: "true"
```

### Built-in Adapters

| Adapter | Transport | Required Config |
|---------|-----------|-----------------|
| Console | stderr via structured logger | none (always available as fallback) |
| ntfy | HTTPS POST to ntfy server | `topic` (required), `server_url` (defaults to `https://ntfy.sh`), `token` (optional) |
| Slack | HTTPS POST to Incoming Webhook | `webhook_url` (required) |
| Email | SMTP with STARTTLS | `host`, `to_addrs` (required), `port`, `username`, `password`, `from_addr`, `use_tls` |

The ntfy and Slack adapters validate webhook URLs against SSRF (private / loopback / link-local IPs rejected). The email adapter enforces STARTTLS when `use_tls` is true.

### Severity Filtering

`min_severity` drops notifications below the threshold before fan-out. Typical production setup uses `info` for console (full log), `warning` for email / ntfy (actionable alerts), and `critical` for paging channels.

### What emits notifications

Three subsystems publish through the dispatcher:

- **Approval gate** (`ApprovalGateService`): INFO on auto-approve, WARNING on timeout-deny, CRITICAL on expiry
- **Budget enforcer** (`BudgetEnforcer`): threshold crossings at warn/critical/hard-stop percentages, plus per-agent daily limit exhaustion
- **Timeout scheduler** (`ApprovalTimeoutScheduler`): approval about to expire, escalation to next approver

See [Notifications design](../design/notifications.md) for the protocol and extension points.

---

## WebSocket Event Channels

The dashboard subscribes to real-time events over `/api/v1/ws`. External consumers can connect the same way. Tickets (one-time tokens) are obtained via `POST /api/v1/auth/ws-ticket` with a valid session. **Preferred flow**: connect without query params, then send `{"action":"auth","ticket":"<ticket>"}` as the first message; this keeps the ticket out of URLs, logs, and browser history. Query-param `?ticket=...` remains supported as a legacy fallback.

### Channel Inventory

| Channel | Events | Producers |
|---------|--------|-----------|
| `tasks` | `TaskStateChanged` | `TaskEngine` mutation pipeline |
| `agents` | `AgentHired`, `AgentFired`, `AgentPromoted`, `PersonalityTrimmed` | `AgentRegistryService`, `AgentEngine` |
| `approvals` | `ApprovalRequested`, `ApprovalApproved`, `ApprovalRejected`, `ApprovalExpired`, `ApprovalInterrupt`, `ApprovalResumed` | `ApprovalGate`, `EventStreamHub` |
| `clients` | `ClientCreated`, `ClientUpdated`, `ClientDeleted` | `ClientController` |
| `budget` | `BudgetThresholdWarn`, `BudgetThresholdCritical`, `BudgetThresholdHardStop` | `BudgetEnforcer` |
| `meetings` | `MeetingScheduled`, `MeetingStarted`, `MeetingTranscript`, `MeetingCompleted` | `MeetingScheduler`, `MeetingOrchestrator` |
| `activity` | unified activity feed stream (lifecycle, task, cost, tool, delegation) | `ActivityFeedService` |
| `scaling` | `ScalingDecisionCreated`, `ScalingDecisionApproved`, `ScalingDecisionExecuted` | `ScalingService` |
| `settings` | `SettingChanged` | `SettingsChangeDispatcher` |

### Subscribe (JavaScript)

```javascript
// 1. Get a one-time ticket (requires an authenticated session cookie)
const res = await fetch('/api/v1/auth/ws-ticket', { method: 'POST', credentials: 'include' })
const { data: { ticket } } = await res.json()

// 2. Open WebSocket without any query param
const ws = new WebSocket('wss://example.synthorg.io/api/v1/ws')

ws.addEventListener('open', () => {
  // 3. Authenticate via the first message (keeps the ticket out of URLs)
  ws.send(JSON.stringify({ action: 'auth', ticket }))
})

ws.addEventListener('message', (event) => {
  const frame = JSON.parse(event.data)
  if (frame.version !== 1) {
    console.warn('Unknown event version', frame)
    return
  }
  if (frame.action === 'auth_ok') {
    // 4. Subscribes are only accepted after auth_ok
    ws.send(JSON.stringify({ action: 'subscribe', channels: ['tasks', 'approvals'] }))
    return
  }
  handleEvent(frame)
})
```

The server emits `{"action": "auth_ok"}` once your ticket is validated; only then are subscribes accepted. Every event frame carries a `version` field (currently `1`). Unknown versions are logged and dropped client-side.

### Wire Protocol Invariants

- Inbound frames (subscribe / unsubscribe / auth / ping) capped at 4 KiB
- Outbound events capped at 32 KiB (oversized events are dropped with `API_WS_EVENT_DROPPED`)
- Per-connection outbound queue bounded at 64 events; slow consumers get backpressure-dropped (`API_WS_BACKPRESSURE_DROPPED`), not a dead socket
- Heartbeats every 20 s with a 10 s pong timeout; missing pong triggers reconnect
- All string fields sanitized (control chars, bidi override, length cap) before being stored or displayed

---

## Integration Recipes

### Slack webhook sink

1. Create an Incoming Webhook in your Slack workspace (`App Settings → Incoming Webhooks → Add New Webhook`).
2. Set the webhook URL as `SLACK_WEBHOOK_URL` in your deployment env.
3. Add the Slack entry to `notifications.sinks` (see config example above).

Verify by triggering an approval: the dispatcher will post a formatted card to the channel within ~500ms.

### Email relay via SMTP sink

Want to route through a corporate SMTP gateway that doesn't accept direct connections? Use an email sink configured to point at your internal SMTP relay:

```yaml
notifications:
  sinks:
    - type: email
      enabled: true
      params:
        host: "smtp-relay.internal"
        port: "465"
        username: "${SMTP_USER}"
        password: "${SMTP_PASSWORD}"
        from_addr: "synthorg@your-company.com"
        to_addrs: "oncall-ai@your-company.com"
        use_tls: "true"
```

### External event sink via WebSocket bridge

External systems that need task / approval events can open a long-lived WebSocket connection and republish to their own bus (Kafka, NATS, webhook). The dashboard uses this same pattern.

Keep one connection per consumer (not per user). Tickets are short-lived; the server emits `{"action":"ticket_expired"}` ~60 s before expiry, so re-request a fresh one and reconnect.

---

## See Also

- [Design: Notifications](../design/notifications.md): `NotificationSink` protocol and dispatcher
- [Design: Communication](../design/communication.md): event stream hub, A2A gateway, projection layers
- [Centralized Logging](centralized-logging.md): shipping logs (vs notifications) to external systems
- [Settings Reference](settings-reference.md): `notifications` namespace settings
