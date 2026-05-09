---
title: Backup and Restore
description: Automated and manual backups, pluggable ComponentHandler protocol, validated restore with safety backup, retention policies, and REST API.
---

# Backup and Restore

The backup system protects persistent data (persistence DB, agent memory, and company configuration) through automated and manual backups with configurable retention policies and validated restore.

---

## Architecture

- **BackupService**: Central orchestrator coordinating component handlers, manifests, compression, and scheduling
- **ComponentHandler protocol**: Pluggable interface for backing up and restoring individual data components
  - `PersistenceComponentHandler`: SQLite `VACUUM INTO` for consistent point-in-time copies
  - `MemoryComponentHandler`: `shutil.copytree` with `symlinks=True` for agent memory data directory
  - `ConfigComponentHandler`: `shutil.copy2` for company YAML configuration
- **BackupScheduler**: Background asyncio task for periodic backups with interruptible sleep via `asyncio.Event`
- **RetentionManager**: Prunes old backups by count and age; never prunes the most recent backup or `pre_migration`-tagged backups

## Backup Triggers

| Trigger | When | Behavior |
|---------|------|----------|
| Scheduled | Configurable interval (default: 6h) | Background, non-blocking |
| Pre-shutdown | `Company.shutdown()` / SIGTERM | Synchronous, skips compression |
| Post-startup | After config load, before accepting tasks | Snapshot as recovery point |
| Manual | `POST /api/v1/admin/backups` | On-demand, returns manifest. **Requires the `Idempotency-Key` header** (RFC-style retry-safe key, max 255 chars); identical keys within 24h return the cached manifest instead of starting a second backup so a 5xx-driven client retry cannot launch concurrent backups and violate the at-most-one-running invariant. Missing or empty header yields HTTP 400. |
| Pre-migration | Before restore operations | Safety net, automatic |

## Restore Flow

1. Validate `backup_id` format (12-char hex)
2. Load and verify manifest (structural validation)
3. Re-compute and verify SHA-256 checksum against manifest
4. Validate component sources (handler-specific checks)
5. Create safety backup (pre-migration trigger)
6. Atomic restore per component (`.bak` rollback on failure)
7. Return `RestoreResponse` with safety backup ID

## Configuration

Backup settings live in the `backup` namespace with runtime editability via `BackupSettingsSubscriber`:

- `enabled`: Toggle scheduler start/stop
- `schedule_hours`: Reschedule interval (1 to 168 hours)
- `compression`, `on_shutdown`, `on_startup`: Advisory (read at use time)
- `path`: Requires restart (not dispatched)

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/admin/backups` | Trigger manual backup |
| `GET` | `/api/v1/admin/backups` | List available backups |
| `GET` | `/api/v1/admin/backups/{id}` | Get backup details |
| `DELETE` | `/api/v1/admin/backups/{id}` | Delete a specific backup |
| `POST` | `/api/v1/admin/backups/restore` | Restore from backup (requires `confirm=true`) |

### Error responses

Every endpoint surfaces a structured RFC 9457 envelope on failure (see
[errors reference](../reference/errors.md)). Status codes are produced
in two layers.

**Layer 1: controller-specific translation** (in
`src/synthorg/api/controllers/backup.py`). The restore endpoint
translates internal-detail exceptions into sanitised
HTTP-aware domain errors so the response body never echoes raw
manifest-parse internals; the original exception is preserved on
``__cause__`` for the structured log emitted by the centralised
handler. The controller does not build its own ``Response`` envelope;
it raises the typed error and the centralised handler maps it.

| Endpoint | Caught exception | Re-raised as | Resulting status |
|----------|------------------|--------------|------------------|
| `POST /api/v1/admin/backups/restore` | `ManifestError` | `ValidationError("Invalid backup manifest")` | `422` |
| `POST /api/v1/admin/backups/restore` | `RestoreError` | `InternalServerException("Restore operation failed")` | `500` |
| `POST /api/v1/admin/backups`, `POST /api/v1/admin/backups/restore` | `BackupInProgressError` | `ConflictError("A backup operation is already in progress")` | `409` |
| `GET /api/v1/admin/backups/{id}`, `DELETE /api/v1/admin/backups/{id}`, `POST /api/v1/admin/backups/restore` | `BackupNotFoundError` | propagated unchanged (carries `RECORD_NOT_FOUND`) | `404` |

**Layer 2: centralised mapping** via `handle_backup_error` in
`src/synthorg/api/exception_handlers.py`.  Catches every `BackupError`
subtype not translated by the controller (for example, `ManifestError`
raised from `GET /api/v1/admin/backups/{id}` since that endpoint does
not translate it explicitly):

| Exception | Status | `error_code` |
|-----------|--------|---------------|
| `BackupNotFoundError` | `404` | `RECORD_NOT_FOUND` |
| `BackupInProgressError` | `409` | `RESOURCE_CONFLICT` |
| Any other `BackupError` subtype (`ManifestError`, `RestoreError`, `RetentionError`, `ComponentBackupError`, plain `BackupError`) | `500` | `INTERNAL_ERROR` (detail `"Backup operation failed"`) |

---

## See Also

- [Persistence](persistence.md): repository protocol, migrations, schema
- [Deployment](deployment.md): container runtime
- [Design Overview](index.md): full index
