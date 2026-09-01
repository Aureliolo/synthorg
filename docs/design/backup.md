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
  - `SQLitePersistenceComponentHandler`: SQLite `VACUUM INTO` for consistent point-in-time copies
  - `PostgresPersistenceComponentHandler`: `pg_dump` / `pg_restore` shellouts with `PGPASSWORD` injected via the child environment (never on argv) and a per-invocation timeout
  - `MemoryComponentHandler`: `shutil.copytree` with `symlinks=True` for agent memory data directory
  - `ConfigComponentHandler`: `shutil.copy2` for company YAML configuration
- **PERSISTENCE_BACKUP_HANDLER_REGISTRY**: `StrategyRegistry` keyed on the backend discriminator ("sqlite" / "postgres"), so swapping SQLite for Postgres at deploy time picks the matching `VACUUM INTO` / `pg_dump` implementation without editing the factory.
  - `_build_persistence_handler` dispatches on the backend **assembled at boot**, taking both its `kind` and its `config` off the one object, and falls back to `config.persistence` only when no backend was built. Reality outranks intent: an env-driven deployment (`SYNTHORG_DATABASE_URL`) builds its backend from a boot config in `api/boot_persistence` that is never written back into `RootConfig`, whose `backend` stays at its `sqlite` default and whose `postgres` block stays `None`. Dispatching on the config alone hands a Postgres deployment a SQLite handler pointed at a file that does not exist, and reading connection details from the config alone leaves the Postgres handler nothing to connect to.
  - A mismatch between the two is logged: at INFO when the boot backend is the more durable one (Postgres-in-env over a default SQLite YAML is the routine compose shape), at WARNING when the configured backend was Postgres and SQLite is what actually came up, since that is a migration that did not take effect.
  - Backup artefacts are written owner-only (`0600` files, `0700` directories): a dump is a complete plaintext copy of the database including every encrypted-at-rest credential blob.
  - The Postgres handler needs `pg_dump` / `pg_restore` on PATH; the backend image ships `postgresql-18-client` for this. `ensure_pg_tools_available` verifies both at factory dispatch, so missing tooling surfaces at boot rather than at the first scheduled backup.
- **BackupScheduler**: Background asyncio task for periodic backups with interruptible sleep via `asyncio.Event`
- **RetentionManager**: Prunes old backups by count and age; never prunes the most recent backup or `pre_migration`-tagged backups

## Backup Triggers

| Trigger | When | Behaviour |
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
- `compression`, `on_shutdown`, `on_startup`: Re-applied onto the live service's config
- `path`: Re-points the live service's backup directory

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

## The admin request the CLI builds

`synthorg backup`, `synthorg restore` and the backup `synthorg wipe` offers to
take are the only callers of this surface that are not a browser, and the Go
CLI builds their requests entirely by hand: it carries no JWT library and no
generated client. Every requirement this API places on them is therefore
written twice, once per language, with nothing but agreement holding the halves
together.

Two requirements, both enforced before any handler runs:

- **A signed system token.** `cli/cmd/backup.go::buildLocalJWT` mints an HS256
  JWT from the shared `jwt_secret`, carrying exactly the six claims
  `AuthService._decode_token_raw` require-lists (`sub`, `iss`, `aud`, `jti`,
  `iat`, `exp`). `JwtClaims` sets `extra="forbid"`, so a surplus claim is
  refused as firmly as a missing one, and the set must match rather than cover.
  `sub` / `iss` / `aud` are pinned by value to `SYSTEM_USER_ID` /
  `SYSTEM_ISSUER` / `SYSTEM_AUDIENCE`; a drift there passes the decode and is
  rejected afterwards by `_enforce_jwt_token_binding`, which looks identical to
  the operator and is not.
- **An `Idempotency-Key` header on both POSTs.** Fresh per invocation, because
  the CLI issues exactly one request and never retries: reusing a key would
  make a deliberate second run inside the 24h window replay the first run's
  manifest. Concurrency is not what this protects. `BackupService._backup_lock`
  owns the at-most-one-running invariant and refuses a second run whatever key
  it carries.

Both halves shipped broken at once, each independently sufficient to refuse
every call, and both suites stayed green because each asserted only its own
side's shape. `check_cli_backend_request_parity.py` derives both halves and
holds them equal on every push; see the CLI Request Parity rule in `CLAUDE.md`.

The path prefix is the third shared value. `api.api_prefix` is operator
overridable, so the CLI reads the same `SYNTHORG_API_API_PREFIX` the backend
does (`cli/internal/config/api_url.go`) rather than keeping a copy that would
404 on every call the moment an operator moved the routes.

---

## When the service cannot be built

`build_backup_service` returns `None` if handler construction fails, and a `None`
service means this process has no backup coverage at all: the scheduler never
starts and no `backup.*` setting has a live consumer, so every knob still renders
in the dashboard and does nothing.

That outcome is reported rather than left silent. `BackupStateSlice.expected`
records that construction was attempted, the startup path logs
`backup.service.unavailable` at ERROR where it skips the wiring, and `/health`
carries a `backup` object: `state` is `wired`, `absent` when construction was attempted
and failed, or `unattempted` when it was never tried, and `detail` carries the
redacted reason when there is one. A bare boolean could say only that backups
are off, never why, which leaves an operator with nothing to act on.

`backup` is deliberately **excluded** from the readiness roll-up. A process with
no backup coverage still serves traffic correctly, so folding it into `/readyz`
would have a supervisor restart a healthy deployment over a condition only an
operator can fix.

---

## See Also

- [Persistence](persistence.md): repository protocol, migrations, schema
- [Deployment](deployment.md): container runtime
- [Design Overview](index.md): full index
