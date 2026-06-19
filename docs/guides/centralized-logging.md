---
title: Centralised Logging
description: Ship SynthOrg logs to centralised storage via syslog, HTTP, or Docker logging drivers.
---

# Centralised Logging

By default SynthOrg writes structured JSON logs to files inside the Docker volume
(`/data/logs/`).  For production multi-instance deployments you need those logs
shipped to a centralised system for aggregation, search, and alerting.

Three approaches are available; they can be combined.

| Approach | Mechanism | Best For |
|----------|-----------|----------|
| **Syslog sink** | App-level `SysLogHandler` shipping JSON to a syslog endpoint | rsyslog, syslog-ng, Graylog |
| **HTTP sink** | App-level batched HTTP POST of JSON log arrays | Any JSON-accepting endpoint; vendor APIs (Loki, Elasticsearch, etc.) need a collector/proxy |
| **Docker logging driver** | Container-level stdout/stderr capture | fluentd, GELF, AWS CloudWatch, GCP Logging |

---

## Syslog Shipping

The `SYSLOG` sink type ships each structured log event as a JSON string to a syslog
endpoint over UDP (default) or TCP.

### Configuration (Runtime JSON)

Add a syslog sink via the `custom_sinks` setting in the **observability** namespace:

```json
[
  {
    "sink_type": "syslog",
    "syslog_host": "syslog.internal",
    "syslog_port": 514,
    "syslog_facility": "local0",
    "syslog_protocol": "udp",
    "level": "info"
  }
]
```

### Configuration (YAML)

```yaml
logging:
  sinks:
    - sink_type: syslog
      syslog_host: syslog.internal
      syslog_port: 514
      syslog_facility: local0
      syslog_protocol: udp
      level: info
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `syslog_host` | Yes | - | Hostname or IP of the syslog receiver |
| `syslog_port` | No | `514` | Port (1--65535) |
| `syslog_facility` | No | `user` | Syslog facility: `user`, `local0` through `local7`, `daemon`, `syslog`, `auth`, `kern` |
| `syslog_protocol` | No | `udp` | Transport: `udp` or `tcp` |
| `level` | No | `info` | Minimum log level to ship |

### Receiver Configuration (rsyslog)

```conf
# /etc/rsyslog.d/50-synthorg.conf
module(load="imudp")
input(type="imudp" port="514")

template(name="synthorg-json" type="string"
         string="%msg%\n")

if $syslogfacility-text == 'local0' then {
    action(type="omfile" file="/var/log/synthorg/app.log"
           template="synthorg-json")
    stop
}
```

### Receiver Configuration (syslog-ng)

```conf
source s_synthorg { udp(port(514)); };
destination d_synthorg { file("/var/log/synthorg/app.log"); };
filter f_synthorg { facility(local0); };
log { source(s_synthorg); filter(f_synthorg); destination(d_synthorg); };
```

### Tips

- **Use TCP** when log loss is unacceptable (TCP retries on connection failure)
- **Use UDP** for high-throughput, low-latency scenarios where occasional loss is tolerable
- **Dedicated facility** (`local0` through `local7`) makes receiver-side filtering straightforward
- **Multiple syslog sinks** with different levels (e.g. ERROR to a pager, INFO to storage) are supported

---

## HTTP Shipping

The `HTTP` sink type batches structured log records and POSTs them as JSON arrays
to an HTTP endpoint. A background thread handles batching, flushing, and retries.

### Configuration (Runtime JSON)

```json
[
  {
    "sink_type": "http",
    "http_url": "https://logs.example.com/ingest",
    "http_headers": [["Authorization", "Bearer <token>"]],
    "http_batch_size": 100,
    "http_flush_interval_seconds": 5.0,
    "http_timeout_seconds": 10.0,
    "http_max_retries": 3,
    "level": "info"
  }
]
```

### Configuration (YAML)

```yaml
logging:
  sinks:
    - sink_type: http
      http_url: https://logs.example.com/ingest
      http_headers:
        - ["Authorization", "Bearer <token>"]
      http_batch_size: 100
      http_flush_interval_seconds: 5.0
      http_timeout_seconds: 10.0
      http_max_retries: 3
      level: info
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `http_url` | Yes | - | Endpoint URL (`http://` or `https://`) |
| `http_headers` | No | `[]` | Extra headers as `[name, value]` pairs |
| `http_batch_size` | No | `100` | Records per POST batch |
| `http_flush_interval_seconds` | No | `5.0` | Seconds between automatic flushes |
| `http_timeout_seconds` | No | `10.0` | HTTP request timeout |
| `http_max_retries` | No | `3` | Retries on failure (0 = no retries) |
| `level` | No | `info` | Minimum log level to ship |

### Endpoint Examples

The HTTP sink ships raw JSON arrays of structured log objects.
Vendor-specific endpoints (e.g., Loki push API, Elasticsearch bulk API)
typically expect a different wire format. Use a generic JSON-accepting
collector (Vector, Fluentd HTTP input, or a lightweight proxy) to
transform the array into the vendor-specific format.

**Generic JSON endpoint** (any receiver accepting JSON arrays):

```json
{
  "sink_type": "http",
  "http_url": "https://logs.example.com/ingest",
  "http_headers": [["X-API-Key", "<key>"]]
}
```

### Tips

- **Tune batch size** based on endpoint limits and network latency
- **Lower flush interval** (1--2s) for near-real-time shipping
- **Set retries to 0** for fire-and-forget shipping where loss is acceptable
- **HTTPS** is recommended for shipping over untrusted networks

---

## Docker Logging Drivers

Docker's built-in logging drivers capture container stdout/stderr. By default the
console sink writes coloured text to stderr. For structured JSON output that Docker
drivers and downstream parsers can process, override the console sink format:

```json
{"__console__": {"json_format": true}}
```

This is applied through the `sink_overrides` runtime setting in the **observability**
namespace; it patches built-in sinks without redefining the whole `sinks` list.

### When to Use Docker Drivers

- Your infrastructure already has a log aggregation pipeline (fluentd, Logstash)
- You want container-level log capture without app-level changes
- You need cloud-native logging (AWS CloudWatch, GCP Logging, Azure Monitor)

### Configuration

In `docker/compose.yml`, replace the default `json-file` driver:

```yaml
# Fluentd driver:
x-logging: &logging
  driver: fluentd
  options:
    fluentd-address: "fluentd:24224"
    tag: "synthorg.{{.Name}}"

# Or for syslog (replace the block above):
# x-logging: &logging
#   driver: syslog
#   options:
#     syslog-address: "udp://syslog.internal:514"
#     syslog-facility: "local0"
#     tag: "synthorg"
```

### Combining with App-Level Shipping

Docker drivers capture only console (stderr) output. The 10 JSON file sinks are
**not** visible to Docker drivers. For complete log coverage:

1. **App-level syslog/HTTP** for routed, structured JSON from all 11+ sinks
2. **Docker driver** as a safety net for console output and uncaught exceptions

---

## Compressed Archival

Rotated log files can be automatically gzip-compressed to save disk space.

### Configuration

Enable compression in the rotation config:

```json
{
  "audit.log": {
    "rotation": {
      "strategy": "builtin",
      "max_bytes": 10485760,
      "backup_count": 10,
      "compress_rotated": true
    }
  }
}
```

Or via the `sink_overrides` runtime setting, which patches an existing sink's
`RotationConfig` without redefining it:

```json
{"audit.log": {"rotation": {"backup_count": 10, "compress_rotated": true}}}
```

### Behaviour

- When `compress_rotated` is `true`, rotated backups are stored as `.log.N.gz` instead of `.log.N`
- Compression happens synchronously during rotation (fast for 10 MB files)
- If compression fails, the uncompressed backup is retained
- The active log file is never compressed; only rotated backups
- Default: `false` (backward compatible)

### Disk Space Savings

Structured JSON logs compress well (typically 5--10x reduction).  With 10 MB rotation
and 10 backup files:

| Setting | Disk Usage |
|---------|------------|
| No compression | ~110 MB (active + 10 backups) |
| With compression | ~20 MB (active + 10 compressed backups) |

---

## Retention Strategy

For production deployments, combine rotation, compression, and shipping:

1. **Ship** all logs to centralised storage (syslog or HTTP) for long-term retention
2. **Rotate** local files with `builtin` strategy (10 MB, 5--10 backups)
3. **Compress** rotated backups to reduce local disk usage
4. **Centralised system** handles search, alerting, and long-term retention

This keeps local disk usage bounded while centralised storage provides the full
audit trail.
