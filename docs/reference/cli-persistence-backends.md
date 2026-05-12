---
title: CLI Persistence Backends
description: SQLite vs Postgres orchestration in the SynthOrg Go CLI: init flags, volume ownership, container hardening, and the auto-wire precedence rule.
---

# CLI Persistence Backends

On-demand reference for `cli/` operators. The short rule in `cli/CLAUDE.md` is: SQLite is the default for single-node / dev; Postgres for multi-instance / production. This page is the orchestration recipe for both.

## Backend matrix

| Backend | Flag | Port | Data volume | When to use |
|---------|------|------|-------------|-------------|
| `sqlite` (default) | `--persistence-backend sqlite` | n/a (in-process) | `synthorg-data` | Single-node, development, small deployments |
| `postgres` | `--persistence-backend postgres` | `3002` (default, override with `--postgres-port`) | `synthorg-pgdata` | Multi-instance, production, high concurrency |

**Interactive mode (TUI)** defaults to PostgreSQL + NATS; **non-interactive mode** defaults to SQLite + internal bus. Override via `--persistence-backend sqlite` / `--bus-backend internal`.

## Volume ownership (`data-init`)

Every generated `compose.yml` includes a `data-init` helper container (busybox) that runs once before the stateful services start. Its job is to chown each named volume to the UID of the non-root user that will own it:

- `synthorg-data` -> `65532:65532` (backend / distroless nonroot).
- `synthorg-pgdata` -> `70:70` with mode `0700` (DHI postgres user; `initdb` requires exclusive 0700 or it aborts with "permissions should be u=rwx (0700) or u=rwx,g=rx (0750)"); only mounted when `--persistence-backend postgres`.
- `synthorg-nats-data` -> `65532:65532` (DHI nats `nonroot` user); only mounted when `--bus-backend nats`.

Fresh Docker named volumes are owned by `root:root` at creation, and DHI images run as non-root with no capability to self-chown, so this one-shot container is required for every backend selection to avoid permission errors. The `postgres` and `nats` services both declare `depends_on: data-init: condition: service_completed_successfully` to block on the chown before starting.

## Postgres orchestration

When `--persistence-backend postgres` is selected, `synthorg init`:

1. Adds a `dhi.io/postgres` DHI (Docker Hardened Image) service to the generated `compose.yml` (read-only rootfs, minimal capabilities via `cap_add`, `pg_isready` healthcheck, named volume `synthorg-pgdata`). The image tag is pinned via `DefaultPostgresImageTag` in `cli/internal/config/state.go` (kept current by Renovate).
2. Extends the `data-init` helper to also chown `synthorg-pgdata` to `70:70` with mode `0700`.
3. Generates a 32-byte URL-safe random password via `crypto/rand` and persists it to `config.json` (`postgres_password`). Re-init preserves the existing password to avoid breaking the running container.
4. Wires `SYNTHORG_DATABASE_URL=postgresql://synthorg:<password>@postgres:5432/synthorg` into the backend container's environment. The SQLite-only `SYNTHORG_DB_PATH` variable is omitted.
5. Sets `SYNTHORG_POSTGRES_SSL_MODE=disable` on the backend because the local DHI postgres inside the docker bridge runs plaintext. Override to `verify-full` for production deployments where TLS terminates at Postgres with trusted certs.
6. Declares `depends_on: postgres: condition: service_healthy` on the backend service so backend startup blocks until Postgres accepts connections.

### Backend auto-wire precedence

In `src/synthorg/api/app.py`: when both `SYNTHORG_DATABASE_URL` and `SYNTHORG_DB_PATH` are present, `SYNTHORG_DATABASE_URL` wins and Postgres is initialized; the SQLite path is ignored. A malformed URL raises loudly at startup rather than silently falling back to a no-persistence install.

### Migration application

`synthorg start` brings up Postgres first (via compose ordering), then the backend applies yoyo migrations on connection.  Yoyo runs in-process via the project's Python venv (no external binary in the runtime image); the `synthorg.persistence.migrations` module wraps it and routes through psycopg 3 via the `postgresql+psycopg://` URL scheme.  `synthorg stop` preserves `synthorg-pgdata` unless `--volumes` is passed.  `synthorg status --wide` reports Postgres container health plus the `synthorg-pgdata` volume size.

### DHI verification

DHI images are verified before pulling via cosign ECDSA signature + SLSA v1 provenance attestation + Rekor transparency log. Verification results are cached in `config.json` (`verified_digests`) and invalidated when Renovate bumps the pinned index digest.

### Port layout

`3000` web / `3001` backend / `3002` postgres / `3003` NATS client. `generate.go` validates port collisions: web vs backend always; postgres vs web/backend/NATS when postgres enabled; NATS vs web/backend when distributed bus mode is active.

## NATS configuration file

When `--bus-backend nats` is selected, `synthorg init` writes `nats.conf` next to the generated `compose.yml` and the NATS service bind-mounts it at `/etc/nats/nats.conf` (read-only). The canonical config content lives in `cli/internal/compose/nats_config.go` (`NATSConfigContent`) and currently sets `max_payload: 16MB`, sized for full LLM agent outputs and meeting transcripts while staying well under NATS's 64MB ceiling.

The helper `writeNATSConfigIfNeeded` keeps the file in sync on every compose write (init, start's digest pin rewrite, `config set`, update's compose refresh) and removes a stale `nats.conf` when switching back to the internal bus.

## Status banner verdict levels

`synthorg status` renders a top-of-screen verdict banner computed by `computeVerdict()` in `cli/cmd/status.go`:

- `OK`: collapses to a single green "All systems operational" line; the happy path stays compact.
- `DEGRADED`: amber box listing recoverable issues (e.g., a service restarting, or distributed bus expected but not wired).
- `CRITICAL`: red box for unrecoverable state (e.g., backend unreachable, persistence not wired when expected, any container unhealthy).

Escalation rules: `CRITICAL` wins over `DEGRADED`, and signals are gated on install expectations: a default internal-bus install is not flagged `DEGRADED` merely because the backend's health response omits `message_bus` (only `--bus-backend nats` installs expect one). An unmatched `--services` filter reports `OK`, not `CRITICAL`, because `renderContainersSection` already explains "No containers match requested services".

## See also

- [cli-config-subcommands.md](cli-config-subcommands.md): `synthorg config get/set/unset/list/path/edit`.
- [cli-env-vars.md](cli-env-vars.md): the full `SYNTHORG_*` env var inventory the CLI honours.
- [persistence-boundary.md](persistence-boundary.md): how the backend itself routes through SQLite vs Postgres repositories.
