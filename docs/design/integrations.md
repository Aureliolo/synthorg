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
| `github` | `token`, `api_url`, `signing_secret` (optional) | `GET /user` |
| `gitlab` | `token`, `api_url`, `signing_secret` (optional) | `GET /user` |
| `gitea` | `token`, `api_url`, `signing_secret` (optional) | `GET /api/v1/user` |
| `forgejo` | `token`, `api_url`, `signing_secret` (optional) | `GET /api/v1/user` |
| `slack` | `token`, `signing_secret` | `POST auth.test` |
| `smtp` | `host`, `port`, `username`, `password` | SMTP EHLO |
| `database` | `dialect`, `host`, `port`, `username`, `password`, `database` | `SELECT 1` |
| `generic_http` | `vendor`, `base_url` (custom only), `token`, `signing_secret` (custom only) | preset probe, else `HEAD base_url` |
| `oauth_app` | `client_id`, `client_secret`, `auth_url`, `token_url` | N/A |
| `a2a_peer` | `base_url`, `auth_scheme`, scheme credentials (`api_key` / `bearer_token` / `client_id` + `client_secret` / mTLS `cert_path` + `key_path`), `signing_secret` | N/A |
| `llm_provider` | `api_key` | N/A |
| `tunnel` | `auth_token` | N/A |
| `deploy` | `token`, `base_url`, `platform`, `environment`, `project` | `HEAD base_url` |
| `registry` | `token`, `base_url`, `provider`, `repository`, `username`, `auth_host`, `channel`, `default_publish_method` | N/A |

The authoritative per-field metadata (label, input type, required/secret flags, capture mode, placement, conditionality) for every type lives in the backend registry `integrations/connections/field_metadata.py`, exposed read-only via `GET /api/v1/connections/types` and the `connections.field_metadata` MCP tool; the dashboard form and the operator console both render from it.

#### Conditional fields

A field can declare `visible_when` / `required_when`: a predicate over another field's current value. A hidden field is not rendered, not validated, and not submitted. This keeps conditional form logic in the payload the backend serves rather than hardcoded in one client, which is what the pure-API-consumer rule demands and what lets the console prompt exactly what the dashboard shows. Three rules are live: a database host is pointless for the embedded dialect, an A2A credential applies only to the auth scheme that uses it, and a generic-HTTP base URL is asked for only when the vendor is `custom`.

The registry validates every condition at import: it must name another field of the same type, that field must not itself be conditionally visible (a hidden field keeps its last value, so chaining would let a stale answer decide a live one), and a condition on a select must name values that select actually offers, bar the empty string that an unanswered select reads as.

#### Vendor presets

`ConnectionType` names a protocol and credential *shape*, never a vendor: a bespoke member exists only where there is vendor-specific behaviour to hang off it (an authenticator, a webhook verifier, a tool family). A service that is an API key over HTTPS has none of that, so it lands on `generic_http` and its identity rides in the record's `metadata` as a `vendor` preset; a deploy platform or registry provider works exactly the same way.

The preset (`integrations/connections/http_vendor.py`) owns the endpoint, the auth header and template, and the health probe's path and query. That single registry is what the connection create path, the health probe and the native web-search provider all read, so a search call and its connection's health check can never disagree about where a service is or how it authenticates. Choosing a preset means the operator is never asked for a base URL the platform already knows; `custom` is the escape hatch that asks for one.

Real vendor names are confined to this declarative registry, the same exemption the LLM provider presets take.

### Secret Storage

Credentials are encrypted at rest via a pluggable `SecretBackend`:

| Backend | Description | Status |
|---------|------------|--------|
| `encrypted_sqlite` | Fernet-encrypted rows in the SQLite `connection_secrets` table (default when persistence = SQLite) | Implemented |
| `encrypted_postgres` | Fernet-encrypted rows in the Postgres `connection_secrets` table (auto-selected when persistence = Postgres) | Implemented |
| `env_var` | Read-only, env var passthrough (no at-rest storage, no OAuth persistence) | Implemented |

Both `encrypted_*` backends share the same Fernet key material (AES-128-CBC + HMAC-SHA256, 32 bytes of key, URL-safe base64). The key is read from the environment variable named by `master_key_env` on each backend's config (default `SYNTHORG_MASTER_KEY`). **Per-secret rotation** (via `SecretBackend.rotate`) writes a new Fernet token under a fresh `secret_id` without touching other rows; losing the key loses only the stored secrets, not the rest of the org data. **Master-key rotation is not supported**: changing `SYNTHORG_MASTER_KEY` makes every previously stored ciphertext undecryptable, so the master key is treated as permanent for the life of the install (re-init preserves it for the same reason).

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
argument it maps to. It is exposed read-only via `GET /api/v1/connections/types` and
the `connections.field_metadata` MCP tool, and the dashboard connection form
renders purely from it (no hand-authored per-type UI), so the console prompts,
the form, and the create call all agree from one definition. The registry stays
in parity with the `required_fields()` each authenticator declares.

### Out-of-band secret capture

A credential (a token, a password, an API key) **never** enters the chat turn,
the persisted transcript, or an LLM prompt. Instead the masked field posts the
raw value straight to
`POST /api/v1/connections/drafts/{draft_id}/fields/{field}/capture`, whose route
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
the console's default `semi` autonomy a sensitive `connections.create`
escalates through the merged auto-gate to the approval inbox with a structured
preview of the exact resolved arguments (secrets masked), so **apply happens
only after an explicit confirm**, enforced by the `ApprovalGate` rather than by
the agent. When the loop needs a secret it calls `connections.request_secret_capture`
(never asking for the value in chat), which surfaces an in-chat masked field; the
dashboard posts the value straight to the capture endpoint and threads only the
single-use handle back on the next `CONFIGURE` turn, so no secret is ever in the
transcript or at the LLM's discretion, and `connections.check_health` verifies the
result. A dedicated deterministic setup controller was considered and deliberately
not built: it would duplicate the governed create/confirm/verify path the console +
approval gate already provide.

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

| Verifier | Connection type | Algorithm | Signature header | Delivery-id header |
|----------|-----------------|-----------|------------------|--------------------|
| `GitHubHmacVerifier` | `github` | HMAC-SHA256, `sha256=` prefix | `X-Hub-Signature-256` | `X-GitHub-Delivery` |
| `GitLabTokenVerifier` | `gitlab` | shared-secret equality (no signing) | `X-Gitlab-Token` | `X-Gitlab-Event-UUID` |
| `GiteaHmacVerifier` | `gitea` | HMAC-SHA256, bare digest | `X-Gitea-Signature` | `X-Gitea-Delivery` |
| `ForgejoHmacVerifier` | `forgejo` | HMAC-SHA256, bare digest | `X-Forgejo-Signature` (falls back to `X-Gitea-Signature`) | `X-Forgejo-Delivery` |
| `SlackSigningVerifier` | `slack` | HMAC-SHA256 (v0 scheme, signs its timestamp) | `X-Slack-Signature` | none |
| `GenericHmacVerifier` | `generic_http` | HMAC-SHA256 | `X-Signature` | none |
| `A2APushVerifier` | `a2a_peer` | HMAC-SHA256 (signs its timestamp) | see [A2A](a2a-protocol.md) | none |

A type absent from this table has no verifier, so `get_verifier` fails closed
rather than applying a generic scheme that would weaken authenticity.

The delivery-id header is what each provider actually sends; it is recorded for
traceability and is deliberately **not** what dedup keys on (see Replay
Protection). No provider sends a generic `X-Nonce` / `X-Request-Id`.

### The signing secret gates ingest

Ingest reads exactly one credential key, `signing_secret`, and refuses any
delivery it cannot authenticate. Every type in the table above therefore exposes
a `signing_secret` field, optional on all but `slack`: a connection is normally
created for outbound API calls, and requiring a webhook secret would block that,
so blank simply means this connection receives no webhooks.

Only that one key is honoured. An alias ingest accepted but no type declared
would be invisible to every metadata-driven surface, including the guard that
refuses secrets sent inline in a create body, so it could open an authenticated
ingest path an operator could not see.

A captured signing secret must be at least 16 non-whitespace characters. The
value is compared against a header on an endpoint reachable without credentials,
and GitLab's scheme binds neither the body nor a timestamp, so a short secret is
guessable within the per-IP rate limit; every provider that mints these issues
far longer ones.

`ConnectionTypeMetadata.webhook_secret_field` reports the field name, and
`webhook_ingest_is_reachable` resolves that field's own `visible_when` against a
connection's stored values. Both the dashboard and ingest itself consult it: a
`generic_http` connection bound to a known outbound vendor preset hides its
signing secret, because such an API is called rather than heard from, and ingest
then refuses deliveries on it rather than authenticating with a secret no surface
still shows.

**Every pre-authentication rejection answers 401 with one message** -- unknown
connection, no verifier for the type, secret unset, signature mismatch. The
endpoint takes no credentials, so distinguishing them would let an
unauthenticated caller enumerate connection names and probe their configuration.
The reason is kept in the structured log.

### Replay Protection

A delivery is identified by the connection it addressed and the bytes it carried,
and by nothing else. `build_delivery_key` composes that identity as
`<len>:<connection_name>:sha256:<body digest>`, and **both** dedup gates key on
it: the in-memory `ReplayProtector` bounds a replay within its window (default 5
minutes), and the durable `IdempotencyService` bounds it for the whole TTL across
replicas.

What is excluded is the point. The body is the only part of the request a
verifier inspects at all, so anything else is attacker-controlled:

- **A header-supplied id.** Keying on one let a single captured body publish
  repeatedly under fresh values. The delivery id is read for logging only.
- **The URL `event_type`.** No verifier takes the path as an input, so a body
  that passes verification passes against *any* path. While the durable key
  included the event name, one captured delivery bought a fresh verified publish
  per name an attacker chose to post it to: enough to drive event names the
  upstream never sent, including a sprint's `transition_event`.

How strongly the body is bound varies by scheme, and the key does not depend on
it: the signing schemes HMAC the body, while the token-equality scheme
authenticates the sender rather than the bytes and binds nothing. For that one
the digest identifies the delivery without evidencing its origin, and there is
nothing stronger available to key on.

The connection name is included for the opposite reason: two connections can
legitimately be sent the same bytes, and the first must not suppress the second.
Both gates take the identical key deliberately. Keyed differently, each dimension
is only as guarded as whichever gate happens to cover it, which is exactly how the
`event_type` widening survived: the in-memory gate never had it.

A signed timestamp, where the scheme provides one (`slack`, `a2a_peer`), is
additionally checked against the dedup window.

Two byte-identical bodies genuinely sent as distinct events therefore collapse
onto one publish. That is the intended trade: identical signed bytes are
indistinguishable from a replay, so a sender that needs two events to be distinct
has to make their bodies distinct.

### Receipt retention

`integrations.webhook_receipt_retention_days` defaults to `0`, which never
sweeps. A positive value sets a window in days; a per-connection
`webhook_receipt_retention_days` overrides the global one, with the same `0`
meaning. The sweep reads the setting live on each tick, so a change takes effect
without a restart.

Note that **no production code path writes a receipt yet**: the repositories and
the read/retry services exist, and the sweep is wired, but nothing populates the
table. Until a writer lands the setting is inert, and the `0` default is a
choice about what should happen once receipts exist rather than a description of
current behaviour. A receipt stores the raw inbound body, so whoever adds the
writer should revisit whether unbounded retention of attacker-authored payloads
is the right default.

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
- **Interval**: the loop wakes every 5 minutes (configurable), but that is not
  how often any one connection is probed. A verdict is trusted for a period
  set by its own outcome: 6 hours for `healthy`, 30 minutes for `degraded`,
  5 minutes for `unhealthy`, each configurable via
  `integrations.health_*_recheck_seconds`. The gap is wide on purpose, because
  a probe against a metered third-party API costs the operator real quota and
  re-proving a working credential every few minutes buys nothing. A rate
  limit's own `Retry-After` raises that floor further: the provider has
  already said when it will answer again.
- **One freshness policy**: the background loop and the aggregate-health
  endpoint share `health/freshness.py`. The endpoint serves the stored verdict
  while it is fresh instead of probing, so opening the Connections view (which
  polls) cannot re-probe the whole catalogue on a timer. That is why the
  stored verdict carries its detail, latency and ingest state, not just a
  status: a cached answer has to be as complete as a live one.
- **Pattern**: Matches the `ProviderHealthProber` design
- **`UNKNOWN` is a no-op**: a checker that cannot probe (e.g. an
  `LLM_PROVIDER` connection with no `base_url`) reports `UNKNOWN`; the prober
  neither resets nor increments the failure counter, so a healthy provider
  never escalates to `UNHEALTHY` over successive cycles.
- **`LLM_PROVIDER`** (`LlmProviderHealthCheck`): prefers the verdict
  `ProviderHealthTracker` already holds, mapping `up`/`degraded`/`down` onto
  `HEALTHY`/`DEGRADED`/`UNHEALTHY`, so the Connections screen and the Providers
  screen cannot disagree about the same provider. Only when the tracker has
  nothing does it fall back to its own probe: GET the connection `base_url`,
  where any sub-500 response is `HEALTHY` (the endpoint is reachable), a 5xx /
  network error / SSRF rejection is `UNHEALTHY`, and a connection with no
  `base_url` (litellm-routed cloud provider) is `UNKNOWN`. The probe is
  SSRF-validated and DNS-pinned before any request.
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
  reports `UNHEALTHY` rather than false-greening on mere reachability. A vendor
  preset supplies the header its API actually accepts and a free metadata
  endpoint to send it to, and never a payload: a health check must not buy
  anything to prove a credential works, or watching a connection would bill
  the operator for it. Where a vendor publishes no such endpoint, the probe
  reads the vendor's own rejection instead: an authentication failure is a
  real fault, a rejection of the request shape proves the credential cleared,
  and anything else is reported `UNKNOWN` rather than guessed at. The response
  body is read under a size cap, since the endpoint is operator-supplied and
  re-read on a loop. This is the connection type the
  native web-search feature binds its API key to. A credential that cannot be
  resolved reports which of the two causes applied: a misconfigured credential
  is deterministic and the operator must re-enter it, whereas a secret store
  that is down is transient and clears itself, and reporting both the same way
  sends the operator to rotate working keys. A 429 is reported as a rate limit
  with its `Retry-After`, not as a generic failure.
- **Probe deadline**: every probe is bounded by one wall-clock deadline rather
  than by per-operation timeouts alone, and the prober applies its own ceiling
  on top. Probes share a task group, so an endpoint that drips bytes just under
  the read timeout would otherwise stall the whole cycle, freezing health
  reporting for every other connection.
- **`REGISTRY`** has no registered checker: a container registry answers only
  under a repository-scoped token exchange, so a generic probe would report
  `UNHEALTHY` for a correctly-configured connection.

### Inbound readiness is reported separately from the probe

Every `HealthReport` also carries `webhook_ingest`
(`not_applicable` / `ready` / `unconfigured`), computed by
`check_connection_health` rather than by any per-type checker: the derivation is
identical for every type, and a checker that forgot it would silently report
`not_applicable` for a connection that does have an inbound path. It follows the
ingest path's own order: the type must declare a signing-secret field, that
field's `visible_when` must hold for this connection's stored values, and a
usable secret must be stored.

It is deliberately **not** folded into `ConnectionStatus`. A signing secret is
optional by design (see [The signing secret gates
ingest](#the-signing-secret-gates-ingest)), so an outbound-only connection would
otherwise read as degraded for lacking something it never needed. But when the
secret *is* what stands between a sender and ingest, every delivery 401s, and a
rejection writes no receipt, so without this field the only trace is a server
log. The dashboard's connection card surfaces `unconfigured` as its own line; the
health badge stays the outbound verdict.

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
MCP server executes untrusted code, so it sits in that set. The policy lives in
`tools/mcp/sandbox.py` and the transport that applies it is
`tools/mcp/container_stdio.py`, which creates the container over the Docker API
and attaches stdin+stdout before starting it, under `--cap-drop=ALL`,
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

Install and uninstall additionally trigger a failure-tolerant runtime
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
- **Dev Tunnels**: drives the `devtunnel` CLI, resolved like `cloudflared` (`PATH`, then `bin/` under the state dir, then, unless `devtunnel_download_enabled: false`, an HTTPS download from Microsoft's fixed `aka.ms/TunnelsCliDownload/*` asset URLs; the licence forbids redistribution, not a runtime download by the operator's own deployment). The product is named "Dev Tunnels"; GitHub is only the sign-in method. The credential is a GitHub device-code login (`POST /device-login` returns the verification URL + one-time code; the CLI completes and stores the login itself). Microsoft offers no credential-injection API (every token-minting command requires an already-logged-in CLI), so unlike the ngrok token the login cannot live in the encrypted catalog; on POSIX the adapter instead confines the login cache owned by the CLI, overriding `HOME` to a private owner-only `devtunnels-home/` under the state dir. Because it stores no token, the manager seeds a read-only, no-secret `tunnel-devtunnels` `Connection` (empty credentials, `health_check_enabled=False`) when the operator begins the device login, so it still appears in the Connections list and is health-checked through the generic tunnel status lookup alongside ngrok. Only the login mints it: the status endpoint is a read, and the dashboard polls it, so seeding there would both create a connection the operator never asked for and recreate one they had just deleted.

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
    receipt_retention_days: 0  # 0 never sweeps
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
