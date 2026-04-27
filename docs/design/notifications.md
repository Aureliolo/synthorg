---
title: Notifications
description: Pluggable NotificationSink protocol, NotificationDispatcher fan-out, severity filtering, built-in adapters (console, ntfy, Slack, email), and integration points.
---

# Notifications

The notification subsystem delivers operator-facing alerts for events that require human attention: approval gate decisions, budget threshold breaches, system errors, and timeout escalations.

---

## NotificationSink Protocol

All notification adapters implement the `NotificationSink` protocol:

- ``async start() -> None``: open external resources (HTTP clients, sockets). Idempotent: a second call is a no-op. Stateless adapters (`Console`, `Email`) implement this as a no-op for protocol uniformity.
- ``async close() -> None``: release resources. Idempotent.
- ``async send(notification: Notification) -> None``: deliver a single notification. Adapters that hold state (`Slack`, `Ntfy`) raise `RuntimeError` if `send()` is called before `start()`.
- ``async __aenter__() / __aexit__(...)``: async context-manager pair that wraps `start()` / `close()`, so a sink instance can be driven directly via `async with sink:` outside the dispatcher (useful in scripts and tests).

The lifecycle is guarded by an `asyncio.Lock` on stateful adapters so concurrent `start()` / `close()` calls collapse to a single open / close. The protocol is intentionally minimal so new adapters (PagerDuty, Teams, Discord, etc.) can be added without modifying dispatcher logic.

## NotificationDispatcher

The `NotificationDispatcher` is itself driven by an `async start()` / `async aclose()` pair (wired into the API lifecycle in `src/synthorg/api/lifecycle_builder.py`). On startup it fans out `start()` to every registered sink through `asyncio.TaskGroup`; sinks whose start fails are dropped from the active set so the rest of the dispatch flow keeps working.

Each `dispatch(notification)` call fans out to the active sinks concurrently via `asyncio.TaskGroup`. Failures in individual sinks are isolated: a failing Slack webhook does not prevent ntfy or email delivery. All errors are logged with structured event constants (`error_type` + `safe_error_description`, never raw `str(exc)` so webhook URLs / SMTP credentials never reach the log sink) and collected into an `ExceptionGroup` that preserves per-sink context.

The dispatcher applies **severity-based filtering**: notifications below the
configured `min_severity` threshold are dropped before fan-out. `aclose()`
tears down all sinks in parallel using the same TaskGroup pattern.

## Adapters

Four built-in adapters are provided:

| Adapter | Transport | Required Config |
|---------|-----------|-----------------|
| **Console** | stderr via structured logger | None (always available as fallback) |
| **ntfy** | HTTPS POST to ntfy server | `topic` (required), `server_url` (defaults to `https://ntfy.sh`), `token` (optional) |
| **Slack** | HTTPS POST to Incoming Webhook | `webhook_url` (required) |
| **Email** | SMTP with STARTTLS | `host`, `to_addrs` (required), `port`, `username`, `password`, `from_addr`, `use_tls` |

The ntfy and Slack adapters validate target URLs against SSRF (private/loopback IP
rejection). The email adapter enforces STARTTLS when `use_tls` is enabled and
rejects partial credentials (username without password or vice versa).

## Integration Points

Three subsystems emit notifications through the dispatcher:

- **Approval gate** (`ApprovalGateService`): Sends notifications when approval items
  are submitted, auto-approved, auto-denied, or expired. Severity maps to approval
  outcome (INFO for auto-approve, WARNING for timeout deny, CRITICAL for expiry).
- **Budget enforcer** (`BudgetEnforcer`): Sends threshold-crossing notifications at
  the configured warn, critical, and hard-stop percentages. Also notifies on
  per-agent daily limit exhaustion.
- **Timeout scheduler** (`ApprovalTimeoutScheduler`): Sends notifications when
  approval items are about to expire or have been escalated to the next approver
  in the escalation chain.

## Configuration

Notifications are configured under the `notifications` key in the company YAML:

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

When no sinks are configured or all configured sinks are disabled, the factory
automatically includes a console sink as a fallback so notifications are never
silently dropped.

---

## See Also

- [Observability](observability.md): structured logging, correlation IDs, sinks
- [Budget](budget.md): threshold alerts that emit notifications
- [Security & Approval](security.md): approval gate alerts
- [Design Overview](index.md): full index
