---
title: Integrations
description: External service connection catalog, OAuth 2.1, webhooks, health checks, MCP catalog, and rate limiting.
---

# Integrations

The integrations layer provides a unified infrastructure for connecting SynthOrg
to external services. It sits underneath every external consumer (MCP servers,
providers, notification sinks, tools) and provides:

- **Connection Catalog**: typed registry for all external service credentials
- **Secret Backends**: pluggable encrypted credential storage
- **OAuth 2.1**: authorization code + PKCE, device flow, client credentials
- **Webhook Receiver**: signature verification, replay protection, event bus bridge
- **Health Checks**: per-type connection health monitoring with background prober
- **Rate Limiting**: tool-side rate limiter via `@with_connection_rate_limit`
- **MCP Catalog**: bundled curated MCP server catalog with install flow
- **Tunnel**: multi-provider webhook tunnel (Cloudflare quick tunnel default, ngrok, Dev Tunnels)

---

## Connection Catalog

Central registry for external service connections. Each connection has a
unique name, a typed connection type, encrypted credentials (via `SecretRef`),
optional rate limiting and health check configuration, and a `sensitive`
flag. When `sensitive` is set, the governed external-access tool routes every
call against the connection (read or write) to human approval, not only write
methods.

### Connection Types

| Type | Auth Fields | Health Check |
|------|------------|--------------|
| `github` | `token`, `api_url` | `GET /user` |
| `gitlab` | `token`, `api_url` | `GET /user` |
| `gitea` | `token`, `api_url` | `GET /api/v1/user` |
| `forgejo` | `token`, `api_url` | `GET /api/v1/user` |
| `slack` | `token`, `signing_secret` | `POST auth.test` |
| `smtp` | `host`, `port`, `username`, `password` | SMTP EHLO |
| `database` | `dialect`, `host`, `port`, `username`, `password`, `database` | `SELECT 1` |
| `generic_http` | `base_url`, `token` / `api_key` | `HEAD base_url` |
| `oauth_app` | `client_id`, `client_secret`, `auth_url`, `token_url` | N/A |
| `tunnel` | `auth_token` | N/A |

### Secret Storage

Credentials are encrypted at rest via a pluggable `SecretBackend`:

| Backend | Description | Status |
|---------|------------|--------|
| `encrypted_sqlite` | Fernet-encrypted rows in the SQLite `connection_secrets` table (default when persistence = SQLite) | Implemented |
| `encrypted_postgres` | Fernet-encrypted rows in the Postgres `connection_secrets` table (auto-selected when persistence = Postgres) | Implemented |
| `env_var` | Read-only, env var passthrough (no at-rest storage, no OAuth persistence) | Implemented |

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
| `GET` | `/api/v1/connections/types` | Connection-type + credential-field metadata registry (label, type, required, secret, capture mode, help, order) |
| `POST` | `/api/v1/connections/drafts/{draft_id}/fields/{field}/capture` | Out-of-band write-only secret capture; returns an opaque single-use handle (request body is excluded from access/request logs) |

---

## Conversational setup

The operator can stand up a connection-bound integration by talking to the
unified chat's operator console (`configure` intent, see
[Self-Improving Company: the operator console](self-improvement.md#interactive-endpoint-one-unified-turn)).
Three backend pieces make this vendor-generic and secret-safe.

### Connection-type metadata registry

A single backend-owned declarative registry
(`integrations/connections/field_metadata.py`) is the sole source of truth for
*what a connection type needs*: per `ConnectionType`, an ordered list of fields
each carrying `name`, `label`, `type`, `required`, `secret`, `capture_mode`
(`masked_field` or `oauth_redirect`), `help_text`, and the `connections.create`
argument it maps to. It is exposed read-only via `GET /connections/types` and
the `connections.field_metadata` MCP tool, and the dashboard connection form
renders purely from it (no hand-authored per-type UI), so the console prompts,
the form, and the create call all agree from one definition. The registry stays
in parity with the `required_fields()` each authenticator declares.

### Out-of-band secret capture

A credential (a token, a password, an API key) **never** enters the chat turn,
the persisted transcript, or an LLM prompt. Instead the masked field posts the
raw value straight to
`POST /connections/drafts/{draft_id}/fields/{field}/capture`, whose route
excludes the request body from logging; the value is written immediately into
the existing `SecretBackend` and the endpoint returns an opaque **single-use,
short-TTL handle** bound to `(conversation_id, draft_id, field, secret_kind)`.
The console references only the handle. `connections.create` (and
`oauth.configure_provider`) accept **handle references, not inline credential
strings**; create resolves and consumes the handle, verifies the binding
(replay protection), and moves the value to permanent per-connection storage
under its own `secret_id` for the normal rotate/retrieve/delete lifecycle. An
abandoned draft's handle expires and its backend entry is swept. As
defence-in-depth, a deterministic rule blocks any tool result from echoing a
bound secret value back into a turn, and every persisted turn is scanned and
credential-redacted before write (the backstop should never fire; if it does,
it signals a boundary leak). Each field's `capture_mode` in the metadata
registry selects how the value is obtained: static-secret types (a personal
access token, a bot token, an SMTP or Postgres password, a generic API key) use
the masked-field capture above, while a connection type backed by a configured
OAuth app can mark a field `oauth_redirect`, so the value comes from a hosted
authorize flow (the app server only ever receives a short-lived code exchanged
server-side) rather than being pasted.

### Guided flow

There are two equivalent surfaces, and both are secret-safe by the pieces above
rather than by a bespoke wizard.

**Dashboard form (deterministic).** The connection form renders purely from the
metadata registry, captures each `secret` field out of band as it is entered
(masked field -> capture endpoint -> handle), then submits one typed
`connections.create` carrying the non-secret fields inline and the secrets as
`credential_handles` bound to a per-submit `connection_draft_id`. The create is
a single validated call, and the live `connections.check_health` probe leaves
the connection unverified until health passes. This is the deterministic path:
what is submitted is exactly what the form assembled.

**Operator console (conversational).** The same setup runs through the console
`configure` intent as a governed agent loop: it reads the metadata registry,
guides the operator, and calls `connections.create`. Determinism does not come
from a separate step controller; it comes from the platform's governance. Under
the console's default `SEMI` autonomy a sensitive `connections.create`
escalates through the merged auto-gate to the approval inbox with a structured
preview of the exact resolved arguments (secrets masked), so **apply happens
only after an explicit confirm**, enforced by the `ApprovalGate` rather than by
the agent. Secrets are captured out of band by the dashboard's masked field and
referenced only by handle, so no secret is ever at the LLM's discretion, and
`connections.check_health` verifies the result. A dedicated deterministic setup
controller was considered and deliberately not built: it would duplicate the
governed create/confirm/verify path the console + approval gate already provide.

---

## OAuth 2.1

Full OAuth 2.1 implementation with three grant types:

### Authorization Code + PKCE (RFC 7636)

Primary web flow. User clicks "Connect" in dashboard, browser redirects to
provider, callback handler exchanges code for tokens.

### Device Flow (RFC 8628)

For CLI/headless use. Displays user code and verification URL, polls for
authorization.

### Client Credentials

Machine-to-machine flow. No user interaction.

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
- **Pattern**: Matches the `ProviderHealthProber` design
- **`UNKNOWN` is a no-op**: a checker that cannot probe (e.g. an
  `LLM_PROVIDER` connection with no `base_url`) reports `UNKNOWN`; the prober
  neither resets nor increments the failure counter, so a healthy provider
  never escalates to `UNHEALTHY` over successive cycles.
- **`LLM_PROVIDER`** (`LlmProviderHealthCheck`): GETs the connection
  `base_url`; any sub-500 response is `HEALTHY` (the endpoint is reachable),
  a 5xx / network error / SSRF rejection is `UNHEALTHY`, and a connection
  with no `base_url` (litellm-routed cloud provider) is `UNKNOWN`. The probe
  is SSRF-validated and DNS-pinned before any request.
- **`TUNNEL`** (`TunnelHealthCheck`): resolves the same availability +
  credential verdict the dashboard's tunnel card shows, via a
  `TunnelStatusLookup` bound to the tunnel manager at startup
  (`bind_tunnel_status_lookup`). `HEALTHY` when the backing provider is
  available with its credential in place; `UNHEALTHY` when either is
  missing or the status lookup itself fails; `UNKNOWN` when no manager is
  bound yet or the connection maps to no known tunnel provider.
- **`GENERIC_HTTP`** (`GenericHttpHealthCheck`): SSRF-validated, DNS-pinned
  HEAD (GET fallback) of the connection `base_url`. When a `ConnectionCatalog`
  is bound (`bind_catalog`) the probe resolves the connection's credentials
  and sends them as auth headers, so a configured-but-broken credential
  reports `UNHEALTHY` rather than false-greening on mere reachability. This is
  the connection type the native web-search feature binds its API key to.

The stdio MCP bridge exposes a parallel liveness surface:
`MCPToolFactory.server_statuses` records each server's last connect outcome,
and `ping_servers()` live-pings the connected sessions (`list_tools`) so a
child that died after boot surfaces as unhealthy without re-spawning it.

---

## Rate Limiting

`@with_connection_rate_limit` decorator for tool implementations. Reuses
`RateLimiter` from `providers/resilience/rate_limiter.py`.

---

## MCP Server Catalog

Static JSON catalog (`bundled.json`) of curated MCP server entries. It ships the
maintained Brave Search server (`@brave/brave-search-mcp-server`). Forge (GitHub /
Forgejo) and chat (Slack) access is served by first-party, connection-gated agent
tools (`forge_*` / `chat_*`, see [Tools & Capabilities](tools.md)) built on the
native connection catalog + forge/chat client registries, not a third-party MCP
server. Each bundled entry is connection-gated (it declares a `required_connection_type`);
no entry runs without a bound connection. Every stdio entry pins an exact `npm_version` (an
unpinned `npx` spec would resolve `latest` on every reconnect, defeating the
supply-chain pin). A database-typed entry additionally declares
`required_dialect` so entries sharing `ConnectionType.DATABASE` cannot be bound
to a connection of the wrong dialect.

### Credential injection

A catalog entry declares a `credential_env_map` (credential field to environment
variable). The bound connection's secrets are resolved from the connection
catalog and injected into the spawned server's environment **at connect time**,
never persisted into the stored `MCPServerConfig` (which records only the
connection name and the map) and never placed on the process argv (where a
secret would be visible via `ps`/`/proc`). Missing credentials are logged loudly
rather than silently producing an unauthenticated server.

### Sandboxing

A stdio MCP server is arbitrary third-party code (`npx -y <package>@<version>`).
D16 requires the high-risk execution categories to run inside Docker, and an
MCP server executes untrusted code, so it sits in that set. This is a bespoke
launch-rewrite (`tools/mcp/sandbox.py`, `wrap_stdio_in_sandbox`) that runs the
server via `docker run -i` under `--cap-drop=ALL`,
`--security-opt=no-new-privileges`, a read-only rootfs, `NPM_CONFIG_IGNORE_SCRIPTS`,
and cpu/memory/pid limits, controlled by the `tools.mcp_sandbox_*` settings
(sandboxing is on by default, and fails secure to on if the settings cannot be
resolved). It runs parallel to, not through, the per-category `SandboxBackend`
selection the other tool categories use, because the MCP protocol must flow
over the container's stdio. The npm package is version-pinned so a reconnect
never resolves an unexpected `latest`. Resolved secrets are forwarded to the
container by name (`--env KEY`) so they never appear in host `argv`, and
credentials are forwarded only by environment variable (never as a CLI flag).
A failed server connect is isolated so one broken server never blanks the
tools of the others.

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

Install and uninstall additionally trigger a best-effort runtime
hot-reload (`reload_runtime_services`) so a bridged (or removed)
server's tools go live for the next task without a restart; the
startup merge above is the fallback when the runtime is not yet wired
at install time. A reload failure never fails the request (the row is
already persisted) and is logged as `MCP_BRIDGE_RELOAD_FAILED`.

---

## Tunnel

Multi-provider tunnel for local webhook development. A `TunnelManager` facade holds one adapter per provider and delegates start/stop/status to whichever the live `integrations.tunnel_provider` setting selects (resolved fresh at every start, so a Settings change applies without a restart). Starting while a different provider's tunnel is running stops that tunnel first: at most one tunnel is ever up.

Providers:

- **Cloudflare quick tunnel** (default): needs no account; runs `cloudflared tunnel --url` and scrapes the ephemeral `https://*.trycloudflare.com` URL. Binary resolution: `PATH`, then `bin/` under the shared tunnel state dir, then (unless `integrations.tunnel.cloudflared_download_enabled: false`) an HTTPS download of the official Cloudflare GitHub release asset.
- **ngrok**: wraps pyngrok; requires an auth token (ERR_NGROK_4018 refuses anonymous sessions). The token is dashboard-managed: pasted on the tunnel card, stored in the encrypted connection catalog as a `tunnel-ngrok` connection (`ConnectionType.TUNNEL`), and resolved fresh at every start. The env var named in `integrations.tunnel.auth_token_env` (default `NGROK_AUTHTOKEN`) is the headless fallback only.
- **Dev Tunnels**: drives the `devtunnel` CLI, resolved like `cloudflared` (`PATH`, then `bin/` under the state dir, then, unless `devtunnel_download_enabled: false`, an HTTPS download from Microsoft's fixed `aka.ms/TunnelsCliDownload/*` asset URLs; the licence forbids redistribution, not a runtime download by the operator's own deployment). The product is named "Dev Tunnels"; GitHub is only the sign-in method. The credential is a GitHub device-code login (`POST /device-login` returns the verification URL + one-time code; the CLI completes and stores the login itself). Microsoft offers no credential-injection API (every token-minting command requires an already-logged-in CLI), so unlike the ngrok token the login cannot live in the encrypted catalog; on POSIX the adapter instead confines the login cache owned by the CLI, overriding `HOME` to a private owner-only `devtunnels-home/` under the state dir. Because it stores no token, the manager seeds a read-only, no-secret `tunnel-devtunnels` `Connection` (empty credentials, `health_check_enabled=False`) lazily at status/first-login so it still appears in the Connections list and is health-checked through the generic tunnel status lookup alongside ngrok.

The manager is wired **unconditionally** (not gated by `integrations.enabled`) so the dashboard tunnel card is always functional; the tunneled port is the API's own resolved `api.server_port`. Credential storage requires connected persistence (the catalog); everything else works without it.

All tunnel runtime state roots at the tunnel state dir: `SYNTHORG_TUNNEL_STATE_DIR` (registry key `integrations/tunnel_state_dir`, read-only post-init, `..` components rejected at boot), defaulting to `~/.synthorg` bare-metal. The CLI-generated compose sets `/data/tunnel`, so downloaded binaries and the `devtunnel` login survive container recreation even though the backend rootfs is read-only and has no `HOME`.

`GET /integrations/tunnel/status` returns a `TunnelSnapshot`: the public URL, selected + active provider, and per-provider readiness (`available`, `credential_kind`, `credential_configured`, `detail`) so the dashboard renders the provider picker generically without ever transmitting a token.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/integrations/tunnel/start` | Start the selected provider's tunnel |
| `POST` | `/api/v1/integrations/tunnel/stop` | Stop the running tunnel |
| `GET` | `/api/v1/integrations/tunnel/status` | `TunnelSnapshot` (URL, selection, provider readiness) |
| `PUT` | `/api/v1/integrations/tunnel/credential` | Store/rotate a token-kind provider's auth token |
| `DELETE` | `/api/v1/integrations/tunnel/credential/{provider}` | Delete a stored auth token (idempotent) |
| `POST` | `/api/v1/integrations/tunnel/device-login` | Begin a device-code login (Dev Tunnels) |

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
    cloudflared_download_enabled: true
    devtunnel_download_enabled: true
  mcp_catalog:
    enabled: true
```

`integrations.tunnel.auth_token_env` names the environment variable holding the headless-fallback ngrok token (the dashboard-managed catalog credential always wins). `cloudflared_download_enabled: false` / `devtunnel_download_enabled: false` require the respective operator-installed binary on `PATH`. The tunnel state dir is env-only (`SYNTHORG_TUNNEL_STATE_DIR`), not YAML. The active provider is the `integrations.tunnel_provider` **setting** (ENUM `cloudflare` / `ngrok` / `devtunnels`, default `cloudflare`; DB > env > default), not static YAML.

---

## Provider Migration

`ProviderConfig` now supports a `connection_name` field that references a
connection in the catalog. When set, credentials are resolved from the
catalog at runtime instead of using embedded `api_key` / OAuth fields.

## MCP Service Facades

The integrations domain exposes six service facades on `AppState` for
MCP handler shims:

| Facade | Module | Tools shimmed |
|---|---|---|
| `ClientFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_clients_list`/`_get`/`_create`/`_deactivate`/`_get_satisfaction` |
| `ArtifactFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_artifacts_list`/`_get`/`_create`/`_delete` |
| `OntologyFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_ontology_list_entities`/`_get_entity`/`_get_relationships`/`_search` |
| `MCPCatalogFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_mcp_catalog_list`/`_search`/`_get`/`_install`/`_uninstall` |
| `OAuthFacadeService` | `synthorg.integrations.mcp_services` | `synthorg_oauth_list_providers`/`_configure_provider`/`_remove_provider` |
| `TunnelService` | `synthorg.integrations.tunnel.mcp_service` | `synthorg_tunnel_get_status`/`_connect` |

All destructive operations (`_delete`, `_deactivate`, `_uninstall`,
`_remove_provider`) route through `require_admin_guardrails()` and
emit `MCP_ADMIN_OP_EXECUTED` on success. Artifact delete performs
storage deletion before index removal so the two cannot diverge silently.
