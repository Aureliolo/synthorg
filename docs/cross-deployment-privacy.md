# Cross-Deployment Analytics Privacy Policy

Cross-deployment analytics is an **opt-in** feature that aggregates anonymized improvement outcomes across multiple SynthOrg deployments to identify patterns and recommend improved default thresholds.

## What Is Collected

When enabled, the following anonymized fields are sent to the configured collector endpoint after each proposal decision and rollout result:

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | String | Wire format version (currently `"1"`) for forward compatibility |
| `deployment_id` | Salted SHA-256 hash | Non-reversible identifier for deployment correlation |
| `event_type` | Enum | `proposal_decision` or `rollout_result` |
| `timestamp` | Date (day only) | ISO 8601 date, no time component |
| `altitude` | Enum | Proposal altitude (config_tuning, architecture, etc.) |
| `source_rule` | String | Built-in rule name or `"custom"` for user-defined rules |
| `decision` | Enum | `approved` or `rejected` (decisions only) |
| `confidence` | Float | Proposal confidence score (0-1) |
| `rollout_outcome` | Enum | `success`, `regressed`, `rolled_back`, `failed`, `inconclusive` |
| `regression_verdict` | Enum | `no_regression`, `threshold_breach`, `statistical_regression` |
| `observation_hours` | Float | Rollout observation window duration |
| `enabled_altitudes` | List | Which altitudes are enabled (categorical, not config values) |
| `industry_tag` | String | User-provided industry category (opt-in) |
| `sdk_version` | String | SynthOrg version |

## What Is NOT Collected

The following fields are **explicitly dropped** during anonymization and never leave the deployment:

- **Company names, org identifiers**: Not present in any event
- **Agent IDs, agent names**: Not present in any event
- **User names** (`decided_by`): Dropped
- **Free-text reasons** (`decision_reason`, `details`): Dropped
- **Proposal titles and descriptions**: Dropped
- **Config values**: Only categorical altitude names are sent, never actual thresholds or settings
- **Raw UUIDs** (`proposal_id`): Dropped (deployment_id is a salted hash, not a UUID)
- **Exact timestamps**: Coarsened to day granularity (no time, no timezone)
- **Custom rule names**: Mapped to generic `"custom"` to prevent logic leakage

## How Anonymization Works

1. **Strict allowlist**: Only the fields listed above survive. Everything else is dropped.
2. **Salted hashing**: The `deployment_id` is computed as `SHA-256(salt)` where the salt is a secret string configured by the deployment operator. Changing the salt invalidates all correlation.
3. **Timestamp coarsening**: Timestamps are truncated to day granularity (`YYYY-MM-DD`), removing time-of-day information that could enable timing correlation.
4. **Rule classification**: Custom rule names are replaced with the generic string `"custom"` to prevent leaking deployment-specific detection logic.

The anonymization is implemented as a **pure function** (`anonymize_decision` / `anonymize_rollout`) that is easy to audit and test.

## How to Opt In

Cross-deployment analytics is **disabled by default**. To enable:

```yaml
self_improvement:
  cross_deployment_analytics:
    enabled: true
    collector_url: "https://your-collector.example.com/api/meta/analytics"
    deployment_id_salt: "your-secret-salt-string"
    industry_tag: "technology"  # optional
```

Required fields when `enabled: true`:
- `collector_url`: HTTPS endpoint to POST anonymized events to
- `deployment_id_salt`: Secret salt for deployment identification

## How to Inspect Events

Enabling DEBUG logging for the `synthorg.meta.telemetry` logger emits queue/flush operational metadata (event type, queue depth, batch size, HTTP status). The full serialized event payload is **not logged**; only diagnostics metadata is emitted:

```yaml
logging:
  per_logger_levels:
    synthorg.meta.telemetry: DEBUG
```

Events are visible in the structured log output with event metadata (event type, queue depth, batch size, HTTP status). Note: the full serialized event payload is not logged; only operational metadata is emitted for diagnostics. Event names:
- `cross_deployment.event.queued`: event buffered (logs event_type and pending count)
- `cross_deployment.batch.flushed`: batch sent to collector (logs event_count and HTTP status)

## Data Retention

The default collector (`InMemoryAnalyticsCollector`) stores events **in memory only** with a bounded FIFO buffer (default: 100,000 events). When the cap is exceeded, the oldest events are evicted. Data is lost on restart. There is no persistent storage in the default configuration. The `max_events` capacity is configurable at construction time.

## Access Control

The collector endpoint (`POST /api/meta/analytics/events`) requires **write access** and is protected by the standard API authentication and authorization middleware. Pattern and recommendation queries (`GET`) require read access.

## Collector Role

A deployment can optionally act as a collector that receives events from other deployments:

```yaml
self_improvement:
  cross_deployment_analytics:
    enabled: true
    collector_url: "https://this-deployment/api/meta/analytics"
    deployment_id_salt: "salt"
    collector_enabled: true  # enable collector role
```

The collector never sees unanonymized data; it only receives the anonymized events described above.
