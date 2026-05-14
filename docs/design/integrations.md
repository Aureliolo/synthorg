---
title: Integrations
description: External service connection catalog, OAuth 2.1, webhooks, health checks, MCP catalog, and rate limiting.
---

# Integrations

The integrations layer provides a unified infrastructure for connecting SynthOrg
to external services.  It sits underneath every external consumer (MCP servers,
providers, notification sinks, tools) and provides:

- **Connection Catalog**: typed registry for all external service credentials
- **Secret Backends**: pluggable encrypted credential storage
- **OAuth 2.1**: authorization code + PKCE, device flow, client credentials
- **Webhook Receiver**: signature verification, replay protection, event bus bridge
- **Health Checks**: per-type connection health monitoring with background prober
- **Rate Limiting**: tool-side rate limiter via `@with_connection_rate_limit`
- **MCP Catalog**: bundled curated MCP server catalog with install flow
- **Tunnel**: ngrok adapter for local webhook development

---

## Connection Catalog

Central registry for external service connections.  Each connection has a
unique name, a typed connection type, encrypted credentials (via `SecretRef`),
and optional rate limiting and health check configuration.

### Connection Types

| Type | Auth Fields | Health Check |
|------|------------|--------------|
| `github` | `token`, `api_url` | `GET /user` |
| `slack` | `token`, `signing_secret` | `POST auth.test` |
| `smtp` | `host`, `port`, `username`, `password` | SMTP EHLO |
| `database` | `dialect`, `host`, `port`, `username`, `password`, `database` | `SELECT 1` |
| `generic_http` | `base_url`, `token` / `api_key` | `HEAD base_url` |
| `oauth_app` | `client_id`, `client_secret`, `auth_url`, `token_url` | N/A |

### Secret Storage

Credentials are encrypted at rest via a pluggable `SecretBackend`:

| Backend | Description | Status |
|---------|------------|--------|
| `encrypted_sqlite` | Fernet-encrypted rows in the SQLite `connection_secrets` table (default when persistence = SQLite) | Implemented |
| `encrypted_postgres` | Fernet-encrypted rows in the Postgres `connection_secrets` table (auto-selected when persistence = Postgres) | Implemented |
| `env_var` | Read-only, env var passthrough (no at-rest storage, no OAuth persistence) | Implemented |
| `secret_manager_vault` | External secret manager adapter | Stub |
| `secret_manager_cloud_a` | Cloud secret manager (variant A) | Stub |
| `secret_manager_cloud_b` | Cloud secret manager (variant B) | Stub |

Both `encrypted_*` backends share the same Fernet key material (AES-128-CBC + HMAC-SHA256, 32 bytes of key, URL-safe base64). The key is read from the environment variable named by `master_key_env` on each backend's config (default `SYNTHORG_MASTER_KEY`). **Per-secret rotation** (via `SecretBackend.rotate`) writes a new Fernet token under a fresh `secret_id` without touching other rows; losing the key loses only the stored secrets, not the rest of the org data. **Master-key rotation is not currently supported**: changing `SYNTHORG_MASTER_KEY` makes every previously stored ciphertext undecryptable, so the master key is treated as permanent for the life of the install (re-init preserves it for the same reason).

`create_app` auto-promotes the default `encrypted_sqlite` config to `encrypted_postgres` when the active persistence backend is Postgres, so operators do not have to keep the secret backend and persistence backend in manual sync. This automatic selection is the normal path; the only cases that require explicit config are operators who want `env_var` (no at-rest storage) or a custom `master_key_env` variable name. When `SYNTHORG_MASTER_KEY` is unset, both encrypted backends log an **error** and downgrade to `env_var` so the integrations subsystem still boots in a degraded-but-functional state; set the key and restart to re-enable at-rest encryption. The selection logic lives in `resolve_secret_backend_config` (`persistence/secret_backends/factory.py`) and is covered by unit tests for each branch.

`synthorg init` generates a fresh Fernet master key, writes it to `config.json` (`master_key`), and wires it into the backend container as `SYNTHORG_MASTER_KEY` whenever the `Encrypt secrets at rest` advanced toggle is ON (the default). Re-init preserves the existing key so already-stored ciphertext stays decryptable. The toggle can also be flipped via `--encrypt-secrets=true|false` in non-interactive mode.

At-rest protection of the *rest* of the database (non-secret rows, full-text backups, snapshots) is the operator's responsibility: use disk/filesystem encryption (LUKS, BitLocker, FileVault, cloud-provider encrypted volumes, RDS-style encryption at rest). Column-level encryption in the app is deliberately narrow: its goal is to prevent a SQL-level reader from lifting OAuth tokens and API keys, not to substitute for OS/volume encryption.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/connections` | List all connections |
| `GET` | `/api/v1/connections/{name}` | Get connection by name |
| `POST` | `/api/v1/connections` | Create a connection |
| `PATCH` | `/api/v1/connections/{name}` | Update a connection |
| `DELETE` | `/api/v1/connections/{name}` | Delete a connection |
| `GET` | `/api/v1/connections/{name}/health` | On-demand health check |
| `GET` | `/api/v1/connections/{name}/secrets/{field}` | Scoped reveal of a single credential field (audit-logged; returns a generic 404 on any failure to avoid side-channel leakage) |

---

## OAuth 2.1

Full OAuth 2.1 implementation with three grant types:

### Authorization Code + PKCE (RFC 7636)

Primary web flow.  User clicks "Connect" in dashboard, browser redirects to
provider, callback handler exchanges code for tokens.

### Device Flow (RFC 8628)

For CLI/headless use.  Displays user code and verification URL, polls for
authorization.

### Client Credentials

Machine-to-machine flow.  No user interaction.

### Token Lifecycle

`OAuthTokenManager` background service refreshes tokens before expiry
(configurable threshold, default 5 minutes).

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/oauth/initiate` | Start OAuth flow |
| `GET` | `/api/v1/oauth/callback` | OAuth provider callback |
| `GET` | `/api/v1/oauth/status/{connection_name}` | Token status |

---

## Webhook Receiver

Generic webhook endpoint that verifies signatures and publishes events to the
SynthOrg message bus.

### Signature Verifiers

| Verifier | Algorithm | Header |
|----------|-----------|--------|
| `GitHubHmacVerifier` | HMAC-SHA256 | `X-Hub-Signature-256` |
| `SlackSigningVerifier` | HMAC-SHA256 (v0 scheme) | `X-Slack-Signature` |
| `GenericHmacVerifier` | Configurable HMAC-SHA256 | Configurable |

### Replay Protection

In-memory nonce + timestamp dedup window (default 5 minutes).

### Event Bus Bridge

Verified events are published to the `#webhooks` channel on the message bus.
`ExternalTriggerStrategy` subscribes and fires workflows on matching events.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/webhooks/{connection_name}/{event_type}` | Receive webhook (202) |
| `GET` | `/api/v1/webhooks/{connection_name}/activity` | Webhook activity log |

---

## Health Checks

Per-type health check implementations with a background `HealthProberService`.

- **Smoothing**: N consecutive failures before marking `unhealthy` (default 3)
- **Interval**: Configurable (default 5 minutes)
- **Pattern**: Matches `ProviderHealthProber`

---

## Rate Limiting

`@with_connection_rate_limit` decorator for tool implementations.  Reuses
`RateLimiter` from `providers/resilience/rate_limiter.py`.

---

## MCP Server Catalog

Static JSON catalog (`bundled.json`) with 8 curated MCP server entries:
GitHub, Slack, Filesystem, PostgreSQL, SQLite, Brave Search, Puppeteer, Memory.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/integrations/mcp/catalog` | Browse all entries |
| `GET` | `/api/v1/integrations/mcp/catalog/search?q=` | Search entries |
| `GET` | `/api/v1/integrations/mcp/catalog/installed` | List installed catalog entries (paginated; powers dashboard hydration on refresh) |
| `GET` | `/api/v1/integrations/mcp/catalog/{entry_id}` | Get single entry |
| `POST` | `/api/v1/integrations/mcp/catalog/install` | Install a catalog entry (dashboard-driven, idempotent) |
| `DELETE` | `/api/v1/integrations/mcp/catalog/install/{entry_id}` | Uninstall a catalog entry (idempotent) |

Installed catalog entries are persisted in the `mcp_installations`
table and merged into the effective `MCPConfig.servers` at bridge
startup via `merge_installed_servers()` in
`synthorg.integrations.mcp_catalog.install`. This keeps dashboard
installs out-of-band from the user-owned YAML config and ensures
they survive restarts without rewriting the config file.

---

## Tunnel

ngrok adapter for local webhook development. The adapter is a standalone wrapper around pyngrok with no persistence, connections, or OAuth dependencies, so it is wired **unconditionally** (not gated by `integrations.enabled`) so the dashboard tunnel toggle is always functional. The adapter only starts a tunnel when the operator explicitly calls `/integrations/tunnel/start`; configuring an auth token via `NGROK_AUTHTOKEN` (or the env var named in `integrations.tunnel.auth_token_env`) unlocks paid-tier behaviour (stable URLs, higher rate caps).

The `GET /integrations/tunnel/status` response includes `has_auth_token: bool` so the dashboard can show free-tier vs paid-tier hints without ever transmitting the token itself.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/integrations/tunnel/start` | Start tunnel |
| `POST` | `/api/v1/integrations/tunnel/stop` | Stop tunnel |
| `GET` | `/api/v1/integrations/tunnel/status` | Get tunnel URL and `has_auth_token` |

---

## Configuration

```yaml
integrations:
  enabled: true
  connections:
    max_connections_per_type: 100
  secret_backend:
    backend_type: "encrypted_sqlite"
  oauth:
    state_expiry_seconds: 3600
    pkce_required: true
    auto_refresh_threshold_seconds: 300
  webhooks:
    rate_limit_rpm: 100
    replay_window_seconds: 300
    max_payload_bytes: 1000000
    verify_signatures: true
  health:
    check_interval_seconds: 300
    unhealthy_threshold: 3
  tunnel:
    auth_token_env: "NGROK_AUTHTOKEN"
  mcp_catalog:
    enabled: true
```

`integrations.tunnel.auth_token_env` names the environment variable the adapter reads its auth token from. The default is `NGROK_AUTHTOKEN`; override only if you keep the token in a differently-named var.


---

## Provider Migration

`ProviderConfig` now supports a `connection_name` field that references a
connection in the catalog.  When set, credentials are resolved from the
catalog at runtime instead of using embedded `api_key` / OAuth fields.

## MCP Service Facades

The integrations domain exposes five service facades on `AppState` for
MCP handler shims:

| Facade | Module | Tools shimmed |
|---|---|---|
| `ClientFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_clients_list`/`_get`/`_create`/`_deactivate`/`_get_satisfaction` |
| `ArtifactFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_artifacts_list`/`_get`/`_create`/`_delete` |
| `OntologyFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_ontology_list_entities`/`_get_entity`/`_get_relationships`/`_search` |
| `MCPCatalogFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_mcp_catalog_list`/`_search`/`_get`/`_install`/`_uninstall` |
| `OAuthFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_oauth_list_providers`/`_configure_provider`/`_remove_provider` |

All destructive operations (`_delete`, `_deactivate`, `_uninstall`,
`_remove_provider`) route through `require_admin_guardrails()` and
emit `MCP_ADMIN_OP_EXECUTED` on success. Artifact delete performs
storage deletion before index removal so the two cannot diverge silently.
