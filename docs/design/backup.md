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
| Manual | `POST /api/v1/admin/backups` | On-demand, returns manifest |
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
in two layers and the layer that runs first wins.

**Layer 1: controller-specific catches** (in
`src/synthorg/api/controllers/backup.py`).  These take precedence
because they run before the central exception handler:

| Endpoint | Caught exception | Status |
|----------|------------------|--------|
| `POST /admin/backups/restore` | `ManifestError` | `422` |
| `POST /admin/backups/restore` | `RestoreError` | `500` (with detail `"Restore operation failed"`) |
| `POST /admin/backups`, `restore` | `BackupInProgressError` | `409` |
| `GET`, `DELETE`, `restore` | `BackupNotFoundError` | `404` |

**Layer 2: catch-all** via `handle_backup_error` in
`src/synthorg/api/exception_handlers.py`.  This is the safety net for
any `BackupError` subtype that is not caught by the controller (for
example, `ManifestError` raised from `GET /admin/backups/{id}` since
that endpoint does not catch it explicitly):

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
