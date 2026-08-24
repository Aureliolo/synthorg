---
title: Security
description: Security architecture, hardening measures, and compliance posture of the SynthOrg framework.
---

# Security

SynthOrg agents act with real tools and real consequences, under an oversight mode you set.
Security is not an afterthought; it is a core architectural concern woven through
every layer of the framework, from the application runtime to the CI/CD pipeline
and container infrastructure.

---

## Application Security

### SecOps Agent & Rule Engine

Every tool invocation passes through a centralised security evaluation pipeline
before execution. The **SecOps service** coordinates a fail-closed rule engine
with five built-in detectors:

| Detector | What It Catches |
|----------|----------------|
| **Policy Validator** | Action type policies (soft-allow / hard-deny / escalate) |
| **Credential Detector** | API keys, passwords, tokens, and private keys in arguments or output |
| **Path Traversal Detector** | `../`, absolute paths, symlink escape attempts |
| **Destructive Operation Detector** | `rm -rf`, `git reset --hard`, destructive shell commands |
| **Data Leak Detector** | PII patterns: emails, SSNs, credit card numbers, phone numbers |

Rules are evaluated sequentially by priority. The first `DENY` or `ESCALATE`
verdict wins. If a rule raises an exception, the engine defaults to **DENY**
(fail-closed). Every decision is recorded in a persistent audit log.

### Output Scanning

After tool execution, the **output scanner** inspects results for sensitive data
using the same credential and PII patterns. Configurable response policies:

- **Redact**: replace matches with `[REDACTED]` before returning to the agent
- **Withhold**: suppress the entire output
- **Log-only**: record the finding, return unmodified output
- **Autonomy-tiered**: different policies per autonomy level

### Approval Workflow

Actions that trigger `ESCALATE` verdicts create approval items with configurable
timeout policies:

- **Wait forever**: block until a human responds
- **Auto-deny**: reject after timeout
- **Tiered**: different timeouts by risk level
- **Escalation chain**: escalate to supervisor on timeout

Tasks are parked (suspended) while awaiting approval and resumed automatically
on resolution.

### Authentication & Authorization

- **HttpOnly cookie sessions**: JWTs are delivered via HttpOnly, Secure, SameSite=Strict cookies (never exposed to JavaScript). Password changes rotate the session cookie so the embedded `pwd_sig` stays current.
- **CSRF protection**: double-submit cookie pattern: a non-HttpOnly CSRF cookie is set alongside the session cookie; JavaScript reads it and sends it as the `X-CSRF-Token` header on mutating requests. The middleware validates header-vs-cookie match using constant-time comparison.
- **Account lockout**: after exceeding a configurable threshold of failed login attempts within a sliding window, the account is temporarily locked (HTTP 429 with `Retry-After` header). Lockout state is restored from SQLite on restart.
- **Refresh token rotation**: optional single-use refresh tokens with replay detection; reuse of a consumed token logs the event and the affected session's refresh tokens are cascade-revoked.
- **Concurrent session limits**: configurable maximum active sessions per user; oldest sessions are revoked when the limit is exceeded.
- **JWT bearer tokens** with password-change detection (`pwd_sig` claim, skipped for system user)
- **System user (CLI)**: internal identity bootstrapped at startup with a random Argon2id password hash. CLI tokens use `sub: "system"` with `iss: "synthorg-cli"` and skip `pwd_sig` validation (JWT HMAC signature is the sole authentication gate). The system user cannot log in, change its password, or be modified through the API.
- **API key authentication** via HMAC-SHA256 deterministic hashing
- **Argon2id password hashing** (time_cost=3, memory_cost=64 MB, parallelism=4)
- **Timing-attack prevention**: dummy hash computation for non-existent users
- **Forced password change**: `must_change_password` flag blocks API access
- **One-time WebSocket tickets**: short-lived (30 s), single-use, cryptographically random tokens exchanged via ``POST /api/v1/auth/ws-ticket`` (requires valid JWT). Prevents long-lived JWT leakage by replacing it with an ephemeral ticket in the WebSocket query parameter. In-memory store, monotonic clock expiry, per-process scope. JWT/API key auth middleware is scoped to HTTP requests only (`ScopeType.HTTP`); WebSocket connections bypass the middleware entirely and rely on handler-level ticket validation.
- **Tiered rate limiting**: three budgets surround the auth middleware, each counting over ``api.rate_limit_time_unit`` (a minute by default, and the figures below are that default). **IP floor** (outermost, un-gated) limits *every* request to 10,000 by client IP, including ones the auth middleware rejects with 401; this protects against floods of forged-token traffic on protected endpoints that would otherwise bypass both user-gated tiers. **Unauthenticated** requests (only when ``scope["user"]`` is ``None``) are limited to 20 by client IP (aggressive cap on brute-force against login/setup/logout). **Authenticated** requests (only when ``scope["user"]`` is set) are limited to 6,000 by user ID (generous budget for normal dashboard usage, keyed by user ID so multi-user deployments behind a shared gateway or NAT do not collectively exhaust a single per-IP bucket). The floor default is sized above the authenticated per-user cap (10,000 ≥ 6,000) so a single user on a shared-NAT deployment cannot accidentally clip themselves on the floor before their per-user tier kicks in; config validation rejects a floor below the authenticated cap. The floor is still the aggregate-IP cap, so many active users behind one NAT can collectively exceed it. Raise ``floor_max_requests`` to ``N × auth_max_requests`` for large shared deployments. Auth runs before both user-gated tiers so ``scope["user"]`` is authoritatively populated for the ``check_throttle_handler`` branch; the IP floor runs before auth so invalid-auth floods still hit a rate cap. All three limits are configurable via ``api.rate_limit.floor_max_requests`` / ``unauth_max_requests`` / ``auth_max_requests`` in the YAML config (or ``SYNTHORG_API_RATE_LIMIT_FLOOR_MAX_REQUESTS`` / ``SYNTHORG_API_RATE_LIMIT_UNAUTH_MAX_REQUESTS`` / ``SYNTHORG_API_RATE_LIMIT_AUTH_MAX_REQUESTS`` environment variables for Docker deployments). All three apply on the next request: `GlobalRateLimitSettingsSubscriber` rebuilds the tier config and swaps it in, and each middleware reads its cap per request. Because loosening any of them widens an anti-abuse boundary, the write itself needs the deliberate confirm + reason + actor guardrail. Only ``rate_limit.exclude_paths`` is compose-set, because exclusions are applied when the middleware is mounted. The health endpoints (``/api/v1/healthz`` and ``/api/v1/readyz``) are excluded from rate limiting by default via ``rate_limit.exclude_paths``. The WebSocket path is excluded from all three tiers; HTTP-style per-request rate limiting is inappropriate for persistent WebSocket connections. In-memory rate-limit storage is single-replica; multi-replica deployments with shared rate limiting require an external store (not yet supported).

    !!! note "Removed field"
        ``RateLimitConfig`` has no ``max_requests`` field. Configurations using
        ``api.rate_limit.max_requests`` are rejected at startup with a validation
        error directing operators to use ``unauth_max_requests`` and
        ``auth_max_requests`` instead.

- **Dedicated auth-endpoint limiter**: separate from the global three-tier
  limiter, a per-minute cap is applied as route-level middleware on the
  brute-force-sensitive auth endpoints (login, setup, change-password, and
  the dev-login bypass), keyed independently of the global tiers so it bounds
  credential-guessing loops regardless of the global budget. Configured via
  ``api.rate_limit_auth_endpoint_max_requests`` (env
  ``SYNTHORG_API_RATE_LIMIT_AUTH_ENDPOINT_MAX_REQUESTS``), read per request
  so a tightening applies at once. Neither the global limiter's master switch
  nor its window unit reaches this tier: turning the general limiter off or
  widening it to per-hour must not relax a brute-force bound.

- **Per-operation rate limiting**: layered on top of the global three-tier
  limiter, individual expensive or abuse-prone operations carry a
  ``per_op_rate_limit`` guard that buckets requests per
  ``(operation, subject)`` via a sliding window. Operations default to
  bucketing by authenticated user ID; external-facing endpoints
  (``webhooks.receive``) bucket by IP. Denials raise
  ``PerOperationRateLimitError`` (HTTP 429, ``error_code=5001``,
  ``error_category=rate_limit``, ``retryable=True``) with a
  ``Retry-After`` header. Pluggable behind a
  ``SlidingWindowStore`` protocol (default: in-memory; Redis reserved
  for cross-worker fairness). Operators tune individual operations
  via ``api.per_op_rate_limit.overrides`` without restart; the
  guard reads the live config on every request. Covers 85+ endpoints
  across memory, providers, agents, tasks, approvals, workflows,
  requests, users, webhooks, custom_rules, ontology,
  departments, connections, personalities, reviews,
  artifacts, backup, oauth, settings, setup, simulations, training,
  a2a, and auth ws-ticket.
- **Per-operation inflight concurrency**: a companion middleware
  (``PerOpConcurrencyMiddleware``) caps simultaneous long-running
  requests per ``(operation, subject)`` for six HIGH-tier endpoints
  (``memory.fine_tune`` shared with ``fine_tune_resume``,
  ``memory.checkpoint_deploy``, ``memory.checkpoint_rollback``,
  ``providers.pull_model``, ``providers.discover_models``). The
  sliding-window guard caps burst rate across time; the inflight
  cap separately enforces "one fine-tune per user at a time" even
  when the window would let a burst through. Denials raise
  ``ConcurrencyLimitExceededError`` (HTTP 429, ``error_code=5002``,
  same envelope shape as ``PerOperationRateLimitError``). Pluggable
  behind an ``InflightStore`` protocol with the same default/Redis
  roadmap. Tuned via ``api.per_op_concurrency.overrides``.

### Provider credential resolution fails closed

A provider config that names a ``connection_name`` is asserting that its calls
carry a credential brokered from the connection catalog. When the catalog
cannot supply one, the driver **raises ``AuthenticationError`` naming the
provider and the connection** rather than dispatching with empty auth material.
Only a config with no ``connection_name`` (the genuinely catalog-less and test
paths) may omit credentials.

The rule exists because the silent path was reachable in production. A
boot-time model-refresh sweep rebuilt the provider registry and installed it
with no catalog bound; the credential resolver read "no catalog" as "omit the
credential" and every subsequent dispatch went out unauthenticated, forever,
behind a single startup log line. Two changes close it:

- ``AppState.swap_provider_registry`` rebinds the always-on credential catalog
  onto any incoming registry before installing it, so a replacement cannot
  arrive unbound; ``bind_credential_catalog(None)`` on a registry that already
  holds one is a refused no-op with a WARNING rather than a silent unbind.
- The credential-catalog construction is **not** inside the
  integrations-feature ``try/except``. A failure there is fatal to provider
  auth, so it is logged at ERROR and re-raised; only the integrations feature
  surface (health prober, OAuth, webhooks, rate-limit coordinator) may fail
  without failing the boot.

### Notification Security

Notification adapter configuration may contain credentials (SMTP passwords,
ntfy tokens). These values are stored in the ``params`` dict of each
``NotificationSinkConfig`` entry in the YAML config. The Slack sink holds no
credential in its params: it names a bound ``SLACK`` connection whose bot
token is brokered from the connection catalog at send time.

- **Credentials in params**: Treat ``password`` and ``token`` params as
  sensitive. Use environment variable substitution in YAML
  (``${SMTP_PASSWORD}``) rather than embedding plain-text secrets.
- **Log redaction**: The observability pipeline's ``sanitize_sensitive_fields``
  processor automatically redacts keys matching ``password``, ``token``, and
  ``secret`` at all nesting depths, so adapter params are not leaked in logs.
- **Transport security**: The email adapter enforces STARTTLS when
  ``use_tls=true`` (default). The ntfy adapter validates that its target URL
  uses HTTPS before sending (SSRF-safe: private/loopback IPs are rejected).
  The Slack sink posts via ``chat.postMessage`` with egress pinned to
  ``slack.com`` by the chat client factory.

### Outbound TLS trust

Two transports dial out and neither reads the other's configuration: the git
child processes (workspace backends, docs engine, agent git tools) and the
httpx clients (forge, chat, deploy, health, A2A). The git hardening in
``core/git_env.py`` deliberately cuts the host's own git configuration out of
the first, which is what stops an operator's ``insteadOf`` rewrite redirecting
a clone, and that necessarily takes the host's TLS trust with it. So trust is
configured in the product instead, once, and both transports read it from
``core/tls_trust.py``:

| Setting | Effect |
|---------|--------|
| ``security.tls_ca_bundle`` | Path to a CA bundle trusted **in addition** to the system trust store. This is the supported way to reach a self-hosted forge behind an internal CA. Blank uses the system store alone. |
| ``security.tls_verify`` | Whether certificates are verified at all. ``true`` by default; ``true -> false`` is a security-weakening write and routes through the confirm+reason+actor guardrail in ``settings/write_governance.py``. |

Additional rather than replacing: a private CA is normally one issuer alongside
the public roots, so naming one must not silently stop trusting everything
else. Verification-off is deliberately available because self-signed hosts are
real and an operator will otherwise reach for something worse, but it trusts
any certificate presented to the product and ``tls_ca_bundle`` is the answer
that does not.

Both are live: a settings subscriber replaces the process-wide snapshot on
write, so the next git command and the next API call use the new value with no
restart.

### Frontend Security

The React dashboard enforces several measures to reduce the client-side attack
surface:

| Measure | Mechanism |
|---------|-----------|
| **XSS prevention** | ESLint `no-restricted-syntax` rule bans `dangerouslySetInnerHTML` at write time. Override requires `// eslint-disable-next-line` with justification. |
| **CSP nonce** | Per-request nonce generated by Caddy (`{http.request.uuid}`), substituted into `<meta name="csp-nonce">` in `index.html` via the `templates` directive, read at runtime by `lib/csp.ts`, and propagated to `CSPProvider` (Base UI) and `MotionConfig` (Motion) so every inline `<style>` tag the app injects carries the nonce. `style-src-elem` is locked to `'self' 'nonce-...'`; see [CSP Nonce Infrastructure](#csp-nonce-infrastructure) below. |
| **Session cookies** | JWTs are stored in HttpOnly, Secure, SameSite=Strict cookies. JavaScript never accesses the token. CSRF is mitigated via the double-submit cookie pattern (non-HttpOnly CSRF cookie + `X-CSRF-Token` header). The 401 interceptor clears auth state on session expiry. |

### CSP Nonce Infrastructure

The dashboard's Content-Security-Policy uses CSP Level 3 directive splitting so that dynamically
injected `<style>` elements are locked to a per-request nonce while inline `style` attributes
retain the narrowly-scoped `'unsafe-inline'` permission they need for layout positioning.

#### How the nonce flows end-to-end

1. **Generation.** `web/Caddyfile` uses Caddy's `{http.request.uuid}` placeholder, which
   produces a UUID (128-bit) per request. The value is stable within a single request, so
   the CSP header and response body both receive the same nonce. Caddy generates the UUID
   from Go's `crypto/rand`. It is cryptographically random.
2. **Injection.** The `templates` directive in the Caddyfile processes `web/index.html` at
   response time, substituting `{{placeholder "http.request.uuid"}}` with the per-request
   UUID. The meta tag uses single-quoted attribute syntax (`content='{{placeholder "http.request.uuid"}}'`)
   so the embedded double-quoted Go template placeholder parses cleanly under HTML parsers
   (parse5, browsers, Vite); the Caddy `templates` engine substitutes the `{{...}}` token
   regardless of outer-quote style. Every HTML response ships with a unique nonce in
   `<meta name="csp-nonce" content='...'>`.
3. **Header.** The `(spa_csp)` snippet in `web/Caddyfile` emits the CSP with the matching
   nonce: `style-src-elem 'self' 'nonce-{http.request.uuid}'; style-src-attr 'unsafe-inline'`.
4. **Runtime read.** On page load, `web/src/lib/csp.ts` (`getCspNonce()`) reads the meta tag,
   rejects the un-substituted Go template placeholder (so local dev where Caddy isn't in
   the path still works), and caches the value.
5. **Propagation.** `web/src/App.tsx` passes the nonce to Base UI's `<CSPProvider nonce>` and
   Motion's `<MotionConfig nonce>`. Every inline `<style>` element injected by these
   libraries (keyframes, pop-up animations, motion values, etc.) carries the nonce.

#### Why the split directives

Under CSP Level 2, `style-src 'unsafe-inline'` allows all inline styles (both `<style>`
elements and `style` attributes). CSP Level 3 separates these into two directives:

- `style-src-elem 'self' 'nonce-...'`: every `<style>` element must either come from the
  same origin or carry the matching nonce. This locks down the higher-risk CSS-injection
  vector (where an attacker-controlled stylesheet can exfiltrate data via attribute-selector
  tricks).
- `style-src-attr 'unsafe-inline'`: inline `style` attributes on DOM elements are still
  permitted. Floating UI (used internally by Base UI for Popover/Menu/Select positioning) sets
  transient inline styles such as `style="position: fixed; top: ...; left: ..."` during
  layout. Per the CSP specification, `style` attributes cannot carry nonces, so this is the
  only directive value that works for them.

#### Why `style-src-attr 'unsafe-inline'` is not a practical XSS vector

- Unlike `<script>` or `<style>` elements, a `style` attribute **cannot execute JavaScript**.
- Data exfiltration via a `style` attribute is limited to single-element visual manipulation:
  no CSS selectors, no `@import`. `url(...)` loads are still gated by `img-src`/`font-src`.
- Same-page UI redress via a malicious `position: fixed; z-index: 99999` is a
  limited-surface attack: an attacker who already has a way to inject a style
  attribute into the dashboard has broader problems, and every interactive
  surface the dashboard exposes is either gated by React event handlers (not
  redressable by a sibling element) or a server-side confirmation step. Note
  that `X-Frame-Options: DENY` does NOT mitigate this case; it prevents the
  dashboard from being framed by a foreign origin, which is a different
  threat.
- The higher-risk CSS-injection vector (attribute-selector exfiltration via a
  malicious `<style>` element) is eliminated by
  `style-src-elem 'self' 'nonce-...'`.
- `script-src` remains locked to `'self'` with no `'unsafe-inline'`.

#### Browser support for directive splitting

- `style-src-elem`: Chrome 75+, Firefox 108+, Safari 15.4+ (partial, full at 26.2+), Edge 79+.
- `style-src-attr`: Chrome 75+, Edge 79+. Not supported in Firefox
  ([bug 1529338](https://bugzilla.mozilla.org/show_bug.cgi?id=1529338)) or Safari.

When a browser does not recognise `style-src-attr`, it falls back to `style-src`; older browsers without `style-src-attr` support still receive the same restriction.

### Security Headers

All API responses include:

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `Permissions-Policy` | `geolocation=(), camera=(), microphone=()` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Cross-Origin-Opener-Policy` | `same-origin` (API); `same-origin-allow-popups` (docs) |
| `Cache-Control` | `no-store` (API, except the conditional-GET allowlist below); `private, max-age=0, must-revalidate` (allowlisted user-scoped reads); `public, max-age=0, must-revalidate` (allowlisted reference data); `no-cache` (dashboard HTML); `public, max-age=31536000, immutable` (dashboard hashed assets); `public, max-age=300` (docs) |
| `Content-Security-Policy` | Strict default; dashboard uses CSP Level 3 directive splitting: `style-src-elem 'self' 'nonce-...'` locks `<style>` elements to the per-request nonce, `style-src-attr 'unsafe-inline'` covers the transient inline positioning styles set by Floating UI. `script-src 'self'` with no `'unsafe-inline'`. See [CSP Nonce Infrastructure](#csp-nonce-infrastructure). Docs UI location has its own relaxed CSP (inline syntax-highlighting requirement of the Material theme). |

### Unauthenticated Probe Disclosure

The two unauthenticated probes are deliberately minimal. `GET /api/v1/healthz` returns
`status` + `uptime_seconds`; `GET /api/v1/readyz` adds only the binary `ok` / `unavailable`
outcome. Neither carries the component topology and neither carries the build version: an
exact version reveals to an anonymous caller precisely which published advisories apply, and no
supervisor or load-balancer decision depends on it. Both live behind authentication on
`GET /api/v1/health`, which requires a read-access role.

---

## Container Hardening

### Distroless Runtime

The backend runs on a **Wolfi-based, apko-composed distroless Python image**: no
shell, no package manager, minimal attack surface. The build uses a 2-stage Dockerfile:

1. **Builder**: compiles dependencies via uv, fixes venv symlink for Wolfi's Python path
2. **Runtime**: apko-composed Wolfi base (no shell, UID 65532)

Base images are declared in `docker/*/apko.yaml` with package specs naming the
series (e.g. `python-3.14`). Exact patch versions are resolved and pinned by
a sibling `apko.lock.json`, refreshed weekly by
`.github/workflows/maint-apko-lock.yml`, and every `apko build` that has one is
handed `--lockfile` so that record constrains the build rather than merely
describing it (enforced by `scripts/check_apko_lock_applied.py`; see
[How a lockfile binds a build](design/deployment.md#how-a-lockfile-binds-a-build)).
`docker/web/apko.yaml` is the one deliberate exception: it depends on a melange
package built during the workflow run, so it has no upstream to lock against
and no lock to apply.
The lock decides *which* versions install; package integrity is Wolfi's signed
`APKINDEX`, which apk verifies whether or not a lock is in play.

### SLSA Build L3

Every published image and CLI archive carries build provenance from
`actions/attest`, generated on GitHub-hosted runners with an
ephemeral, isolated build environment and a signed, non-falsifiable provenance
document. That much is [SLSA](https://slsa.dev/spec/v1.0/levels) Build Level 2.

Level 3 additionally requires the build definition to be isolated from whoever
asks for the build, so a compromised caller cannot alter what gets built while
still producing valid provenance. On GitHub Actions that isolation is a
**trusted reusable workflow**: attestations generated inline in a
directly-triggered workflow reach L2 only. Every signing and attesting step
therefore lives in a reusable workflow, invoked through `workflow_call`:

| Reusable workflow | Signs and attests |
|---|---|
| `.github/workflows/reusable-publish-apko-base.yml` | apko base images |
| `.github/workflows/reusable-publish-image.yml` | backend, sandbox, sidecar, fine-tune |
| `.github/workflows/reusable-publish-image-loaded.yml` | web |
| `.github/workflows/reusable-release-cli.yml` | CLI archives and checksums |

The caller jobs in `build-images.yml` and `verify-cli.yml` pass inputs and
grant token scopes; they run no signing step of their own. Moving one back
inline would silently drop that artifact to L2, so the split is load-bearing
rather than tidiness.

That split also decides what the CLI will accept. Keyless signing derives the
certificate SAN from `job_workflow_ref`, which for a `workflow_call` job is
the reusable workflow's own path rather than the caller's, so the SAN regexes
compiled into the binary (`ExpectedReleaseSANRegex` for release archives,
`ExpectedSANRegex` for images, both in `cli/internal/verify/identity.go`) name
the files in the table above and never the callers. A pin also keeps the name
that signed the
current stable release, because a published signature cannot be re-minted and
dropping it would leave those artifacts permanently unverifiable.

The SAN is only half the identity, and on its own it would be the wrong half.
A reusable workflow in a public repository can be invoked by any repository on
GitHub, and every caller's build produces the *same* `job_workflow_ref`. The
SAN therefore names the build recipe, not the build owner. Both policies also
pin certificate extensions (`SourceRepositoryURI`,
`SourceRepositoryIdentifier`, `RunnerEnvironment`), which is what
distinguishes a build this repository ran from one that merely used its
workflow. Both patterns and both constructors live in
`cli/internal/verify/identity.go`, sharing one builder so the binding cannot
diverge between them: `verify.BuildIdentityPolicy` for images and
`verify.BuildReleaseIdentityPolicy` for release archives. Neither takes a
pattern from its caller. A supplied regex cannot be validated into safety by
inspecting parts of it, because one carrying a second top-level alternative
for an unapproved workflow still opens with the repository prefix and still
closes with the anchor, and the extensions below do not separate the two when
both alternatives name this repository. The numeric repository
identifier is pinned alongside the URI because it survives a rename or
transfer; renaming or transferring this repository would otherwise free the
pinned URI for someone else to claim, so treat both as fixed for as long as
published artifacts must stay verifiable.

Ref classes are pinned per signer rather than shared. A release archive is
only ever cut from a `v*` tag; an image is only ever signed on a push to
`main`, because every publish job is gated to main and retagging re-points a
tag at an already-signed digest without signing again. Accepting a ref class
a signer never legitimately produces would only ever help a forger.

`scripts/check_signing_identity_pins.py` derives the signer set from the
workflow tree, following composite actions and helper scripts as deep as they
go, and fails the push when a pin and the signers disagree, when a declared
signer stops signing, when a signer becomes reachable from a second calling
workflow, or when either constant differs by so much as a character from the
pattern its declaration builds. Without it, moving a signing step passes every
test and breaks `synthorg update` and `synthorg start` for users only after
release.

The claim is enforced, not asserted. `scripts/check_image_signatures.py` runs
as the `verify-signatures` gate after every publish and requires **both** a
cosign signature and a provenance attestation for each pushed digest; an image
whose attestation step silently failed fails the gate rather than shipping. A
signature only proves who pushed the bytes, so signature-only verification
would leave the L3 claim unchecked.

The gate also refuses to run at all when any publish or retag job it depends on
ended in anything but success or skipped. It learns which images to check from
the inventory each publishing job uploads, so a job that stopped between its
push and that upload would otherwise leave tags live in the registry that the
gate never knew to look at, and it would report success having checked only
what it was told about. Because the two are indistinguishable from the job
result alone, a publisher that failed having pushed nothing blocks
certification exactly as one that pushed without recording does.

Verify a published image yourself:

```bash
gh attestation verify oci://ghcr.io/aureliolo/synthorg-backend@sha256:<digest> \
  -R Aureliolo/synthorg
```

To hold the build to the isolated definition rather than merely to this
repository, pin the signer workflow:

```bash
gh attestation verify oci://ghcr.io/aureliolo/synthorg-backend@sha256:<digest> \
  -R Aureliolo/synthorg \
  --signer-workflow Aureliolo/synthorg/.github/workflows/reusable-publish-image.yml
```

### CIS Docker Benchmark

Both backend and web containers enforce CIS v1.6.0 controls in `compose.yml`:

| Control | Setting |
|---------|---------|
| **CIS 5.3** | `security_opt: no-new-privileges:true` |
| **CIS 5.12** | `cap_drop: ALL` |
| **CIS 5.25** | `read_only: true` with `tmpfs` mounts (`noexec`, `nosuid`, `nodev`) |
| **CIS 5.28** | `deploy.resources.limits.pids` per container (256 backend, 64 web) |

Resource limits (`deploy.resources.limits`) cap memory, CPU, and PIDs per container (4G/2CPU/256pids backend, 256M/0.5CPU/64pids web). Log rotation (`json-file` driver, `max-size: 10m`, `max-file: 3`) prevents disk exhaustion.

### Artifact Provenance

- All base images **pinned by SHA-256 digest** (no mutable tags)
- **apko lockfiles** (`docker/*/apko.lock.json`, every base manifest except the deliberately unlocked `docker/web/apko.yaml`) reconciled weekly by `.github/workflows/maint-apko-lock.yml`, and applied at build time via `--lockfile` so the recorded versions are the ones installed (gated by `check_apko_lock_applied.py`)
- **Renovate** auto-updates base-image digests weekly (Saturday mornings) for every Dockerfile (backend, sandbox, sidecar, fine-tune, desktop); the `dockerfile` manager plus `docker:pinDigests` scans them all
- **cosign keyless signing** on every pushed image (Sigstore OIDC-bound)
- **Buildx SPDX SBOMs** (SLSA L1) auto-generated and pushed to GHCR as registry attestations (inspect via `docker buildx imagetools inspect`). Standalone CycloneDX JSON SBOMs are generated separately by Syft. See [Software Bill of Materials](#software-bill-of-materials-sbom) below.
- **Build-level provenance** (SLSA L1) auto-generated by Docker Buildx
- **SLSA Level 3 provenance** for CLI binary releases and container images (generated by `actions/attest`, Sigstore-signed, independently verifiable)
- **Client-side verification**: The CLI (`synthorg start`, `synthorg update`) automatically verifies cosign signatures and SLSA provenance for container images before pulling. Verified digests are pinned in the compose file to prevent tag mutation attacks. Bypass with `--skip-verify` or `SYNTHORG_SKIP_VERIFY=1` for air-gapped environments (not recommended).

---

## Supply Chain Security

### Dependency Management

| Layer | Tool | Policy |
|-------|------|--------|
| Python | `pip-audit` | Per-PR + weekly scan for known CVEs |
| Python | Renovate | Weekly updates, `==` pinned versions, grouped by domain |
| Node.js | `npm audit` | Per-PR, blocks on critical/high |
| Node.js | Renovate | Weekly updates via lockfile (`/web`, `/site`, `/.github`) |
| GitHub Actions | Renovate | Weekly updates, pinned by commit SHA |
| Pre-commit hooks | Renovate | Weekly updates, version-pinned `rev:` tags |
| CI binary tools | Renovate | Weekly updates via regex managers (Trivy, Gitleaks, D2, apko) |
| License | `dependency-review-action` | Permissive-only allowlist (MIT, Apache-2.0, BSD, ISC, etc.) |
| Supply chain | Socket.dev | GitHub App; detects typosquatting, malware, suspicious ownership changes |

### Container Scanning

Every container image is scanned before push:

- **Trivy**: CRITICAL = hard fail, HIGH = warn-only
- **CIS Docker Benchmark**: `trivy image --compliance docker-cis-1.6.0` run against all images (enforced; any FAIL blocks the build)

Images are **only pushed to GHCR after vulnerability scans and CIS benchmark pass**.

### Vulnerability Triage

A finding the project has assessed and chosen not to fix is recorded once, in
`.github/vex/triage.yaml`. Nothing else is hand-edited:
`scripts/generate_vex_documents.py` renders that ledger into the two files
scanners read, and `scripts/check_vex_triage_sync.py` fails the push when
either drifts, when an entry is malformed, or when a re-review date has
arrived. The gate is the expiry mechanism: an assessment nobody can defend
today stops a push, rather than quietly ceasing to suppress at the next scan.

An entry's status decides which file it reaches, and no entry reaches both:

| Status | Rendered into | Who it reaches |
|---|---|---|
| `not_affected` | `.github/vex/synthorg.openvex.json` | Our own scans (`--vex`) **and** every consumer of the image |
| `accepted` (risk accepted) | `.github/.trivyignore.yaml` | Our own scans only; VEX has no status for accepting a risk |

The OpenVEX document is attached as an attestation (`cosign attest --type
openvex`) to every published product image that carries at least one statement,
so the reasoning travels with the bytes. A ledger with no `not_affected` entry
renders an empty document. Nothing is attached in that case, because an empty
claim is not worth a signature. The apko `-base` images are scanned with
`--vex` but carry no attestation of their own, since nothing resolves them as a
product.

Scan by digest, not by tag. The attestation is attached to a digest, and a tag
can be moved to a different image after publication, so a tag-scoped scan can
apply one image's triage to another. It is also the same reference the
verification below takes, which is what lets the two be read together.

```bash
trivy image --vex oci ghcr.io/aureliolo/synthorg-sandbox@sha256:<digest> --show-suppressed
```

It is signed keyless under the same reusable-workflow identity as the build
provenance, and independently verifiable:

```bash
cosign verify-attestation --type openvex \
  --certificate-identity-regexp='^https://github\.com/Aureliolo/synthorg/\.github/workflows/reusable-publish-image(-loaded)?\.yml@refs/heads/main$' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
  --certificate-github-workflow-repository='Aureliolo/synthorg' \
  ghcr.io/aureliolo/synthorg-sandbox@sha256:<digest>
```

Both halves of that identity are load-bearing, for the reason the [SLSA
provenance](#artifact-provenance) section gives at length: our reusable
workflows are public, a `workflow_call` job's certificate names the reusable
workflow rather than its caller, and cosign matches an identity regular
expression by search rather than by full match. An unanchored pattern with no
repository binding would accept an attestation signed by any workflow in any
repository whose name contains ours.

Identity alone still answers a narrower question than it appears to. It
establishes that *an* OpenVEX attestation on this digest was signed by a
workflow allowed to sign one, not that it is the current triage: a digest
published earlier under an older ledger carries that attestation too, and it
satisfies the same policy.

The document's `@id` is a SHA-256 over its own statements, which is what
distinguishes the two. Recomputing it establishes that the statements are the
ones that were signed rather than an edited copy, and it needs nothing but the
attestation, so a consumer can run it without our repository:

```bash
cosign verify-attestation --type openvex \
  --certificate-identity-regexp='^https://github\.com/Aureliolo/synthorg/\.github/workflows/reusable-publish-image(-loaded)?\.yml@refs/heads/main$' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
  --certificate-github-workflow-repository='Aureliolo/synthorg' \
  ghcr.io/aureliolo/synthorg-sandbox@sha256:<digest> \
  | jq -r '.payload | @base64d' \
  | python3 -c '
import hashlib, json, sys

predicate = json.load(sys.stdin)["predicate"]
canonical = json.dumps(
    predicate["statements"], sort_keys=True, separators=(",", ":")
)
digest = hashlib.sha256(canonical.encode()).hexdigest()
identifier = predicate["@id"]
if not identifier.endswith(digest):
    raise SystemExit("VEX document does not match its own statements")
print("OpenVEX document intact:", identifier)
'
```

We hold ourselves to the stronger version of this. The publish job runs
`.github/scripts/cosign_verify_attestation_with_retry.sh`, which performs the
same identity check and then compares the attested `@id` against the document
that run rendered, so publishing fails rather than leaving an older
attestation standing in for the current triage. That comparison needs the
rendered document, which a consumer does not have; the recomputation above is
the part that travels.

**Residual gap.** Trivy does **not** verify the signature of a VEX attestation
it discovers, so anyone able to push to an image repository can attach one that
suppresses findings. `--vex oci` is therefore a trust decision about the
registry and the publisher, and the `cosign verify-attestation` command above is
what turns it back into a checkable claim. Our own gates never use `--vex oci`
for the same reason: they read the reviewed file out of the repository, and a
`not_affected` statement that matches nothing has no ignore-file fallback behind
it, so the finding resurfaces and fails the scan instead of shipping a document
that silently claims more than it delivers.

### Signed Artifacts

- **Container images**: cosign keyless signatures (verify via `cosign verify`) + SLSA Level 3 provenance attestations (verify via `gh attestation verify`), plus an OpenVEX attestation on any product image whose [vulnerability triage](#vulnerability-triage) carries a statement (verify via `cosign verify-attestation --type openvex`)
- **CLI binaries**:
  - cosign keyless signature on checksums file (verify via `cosign verify-blob`)
  - SLSA Level 3 provenance attestations (verify via `gh attestation verify`)
  - Sigstore provenance bundle (`.sigstore.json`, verify via `cosign verify-blob-attestation`)
- **Git commits**: GPG/SSH signed (enforced by branch protection ruleset)
- **GitHub Actions**: All actions pinned by full SHA commit hash
- **GitHub Releases**: Immutable releases enabled; once published, assets and body cannot be modified (prevents supply chain tampering). Releases are created as drafts by Release Please, finalised after all assets are attached.

### Software Bill of Materials (SBOM)

Every release includes CycloneDX JSON SBOMs for all released artifacts:

- **Container images**: per-image SBOMs (`sbom-backend.cdx.json`, `sbom-web.cdx.json`,
  `sbom-sandbox.cdx.json`) generated by [Syft](https://github.com/anchore/syft),
  attached to GitHub Releases as downloadable assets
- **CLI binaries**: per-archive SBOMs (e.g. `synthorg_linux_amd64.tar.gz.cdx.json`)
  generated by GoReleaser + Syft, attached to GitHub Releases
- **Registry attestations**: Buildx-generated SPDX SBOMs pushed to GHCR alongside
  each image (inspect via `docker buildx imagetools inspect`)

---

## CI/CD Security

### Pre-Commit Hooks

Every commit is checked locally before it reaches the remote:

- **gitleaks**: secret detection on every commit
- **hadolint**: Dockerfile linting
- **ruff**: Python linting and formatting
- **commitizen**: conventional commit message enforcement
- **Large file prevention**: blocks files over 1 MB

Pre-push hooks run **mypy type checking** and **unit tests** as a fast gate.

### Continuous Integration

| Check | Gate |
|-------|------|
| Ruff lint + format | Required |
| mypy strict type-check | Required |
| pytest + 80% coverage | Required |
| pip-audit (Python CVEs) | Required |
| npm audit (Node.js CVEs) | Required |
| hadolint (Dockerfile lint) | Required |
| All checks must pass | `ci-pass` required status check |

### Security Scanning

| Scanner | Scope | Schedule |
|---------|-------|----------|
| **gitleaks** | Secret detection (push/PR + weekly) | Continuous |
| **CodeQL** | Static analysis (GitHub Advanced Security) | On push/PR |
| **zizmor** | GitHub Actions workflow security | On push/PR |
| **ZAP DAST** | Dynamic API scan against OpenAPI spec | On push to main + weekly |
| **OSSF Scorecard** | Supply chain maturity scoring | Weekly + on push |
| **Trivy** | Container vulnerability scanning + CIS compliance | On image build |
| **Socket.dev** | Supply chain attack detection | On PR |
| **dependency-review** | License + vulnerability review | On PR |

### DAST Tuning

The ZAP API scan runs with a rules file (`.github/zap-rules.tsv`) that
suppresses validated false positives and informational findings:

| Rule | ID | Action | Rationale |
|------|----|--------|-----------|
| Unexpected Content-Type | 100001 | Ignore | `/docs` intentionally serves Scalar UI HTML |
| Client Error Responses | 100000 | Ignore | ZAP sends literal path params, expected 4xx |
| Base64 Disclosure | 10094 | Ignore | Every base64-decodable value this API returns is opaque by construction and goes only to the requester who already holds the data: HMAC-signed pagination cursors (a tampered one raises rather than decoding), the `csrf_token` cookie, which is a random token the double-submit pattern requires JavaScript to read, and the one-time reveal of an API key or WebSocket ticket to the caller that just minted it. Session and refresh JWTs never appear in a response body; they travel in HttpOnly cookies. |
| Sec-Fetch-* Missing | 90005 | Ignore | CSRF is mitigated via the double-submit cookie pattern; Sec-Fetch-* headers are defence-in-depth but not required, and enforcing them would break non-browser API clients |
| User Agent Fuzzer | 10104 | Ignore | An active-scan fuzzing technique that varies the `User-Agent` header; unrelated to server behaviour and not a finding. |
| Application Error Disclosure | 90022 | Ignore | SynthOrg's RFC 9457 / ProblemDetail envelopes set `ErrorCategory.INTERNAL` with the title `"Internal Server Error"` (see `category_title` in `src/synthorg/core/error_taxonomy.py`), so the rule's substring match will trigger for our legitimate structured 5xx responses. Regression coverage for actual debug-page leaks lives in the exception-handler unit tests. |
| Debug Error Messages | 10023 | Ignore | Same trigger and rationale as 90022. |
| Cookie No HttpOnly Flag | 10010 | Ignore | The `csrf_token` cookie is intentionally configured non-HttpOnly (`httponly=False` in `src/synthorg/api/auth/cookies.py`) as part of the double-submit CSRF pattern: the frontend reads the cookie and echoes its value back in the `X-CSRF-Token` header. Suppressing the rule prevents recurring noise on this intentional configuration. The auth/session cookie itself is HttpOnly. |
| Authentication Request Identified | 10111 | Ignore | Under the current ZAP ruleset, this rule labels endpoints carrying a `password` field for the scanner's own auth-flow inference; not a security finding. |
| Sensitive Information in URL | 10024 | Ignore | Under the current ZAP ruleset matching behaviour, this rule fires when a URL contains substrings such as `session`. In SynthOrg, `session_id` query parameters reference domain runtime/agent session resources (a workflow ID), not HTTP/auth session tokens; auth state is carried in HttpOnly cookies. Two URL-borne values this row does not deny, since both are deliberate and neither is what the rule reported: the WebSocket upgrade accepts a one-time auth ticket as `?ticket=` (single-use, short-TTL, consumed before the socket is accepted, and unused by the shipped dashboard, which sends it in the first frame), and `/oauth/callback` receives `code` and `state`, which is the OAuth2 protocol itself. Behaviour can vary across ZAP versions, so revisit on upgrade. |
| Storable and Cacheable Content | 10049 | Ignore | Nearly every API response is `no-store`, pinned by the security-headers `before_send` hook. The conditional-GET allowlist in `src/synthorg/api/etag.py` deliberately replaces that header on the reads it serves ETags for, including authenticated ones (`/api/v1/settings`, `/api/v1/security/audit`, `/api/v1/tasks`, `/api/v1/activities`), which answer `private, max-age=0, must-revalidate`: no shared cache may store them, and a browser must revalidate before every reuse. The rule cannot tell that revalidated exchange apart from a stored body served without a check, and the sibling alert on the same ID (Non-Storable Content) is pure information. A unit test pins the directive string, so a weaker policy fails the suite. |
| PII Disclosure | 10062 | Ignore | The rule reports any run of 12 or more digits that starts with a card BIN prefix and passes a Luhn check. Every error response carries an RFC 9457 `instance` field holding the per-request correlation ID, a UUID4; a dashed UUID ends in exactly 12 hex characters bounded by `-` and a quote, which is the word boundary pair the Maestro arm of the rule anchors on, so a tail that happens to be all decimal digits and Luhn-valid is reported as a card number. Measured rate: 1.1e-5 per UUID4. No card, bank account, SSN or national-ID data exists anywhere in the product, and the suppression cannot be scoped to a URL because every entity ID in every response has that shape. Unit tests pin the field set of the shared error envelope and of the endpoint that was flagged; card and SSN patterns in agent tool traffic are matched by the fail-closed detector in `src/synthorg/security/rules/data_leak_detector.py`. Full triage in `.github/zap-rules.tsv`. |

The rules file is reviewed when ZAP or the API surface changes.
**When upgrading the ZAP action, the bundled ZAP version, or the
ruleset it uses,** revisit each Ignore row above to confirm the
underlying rule's matcher behaviour, severity, and rule ID have not
changed. Action wrapper bumps and ZAP-engine bumps both alter what
each rule fires on; do not skip the revisit just because the action
version changed by a single minor bump.
Cache-Control is path-aware: API data endpoints use `no-store` to prevent
sensitive data caching, the web dashboard entry point (`index.html`) uses
`no-cache` to force revalidation on every request (ensuring fresh deployments),
content-hashed dashboard assets (`/assets/*`) use `public, max-age=31536000,
immutable` for long-lived caching, and documentation endpoints (`/docs/*`)
allow brief client and proxy caching since they serve public, non-user-specific
content. The one deliberate exception is the conditional-GET allowlist in
`src/synthorg/api/etag.py`, which replaces `no-store` on the reads it serves
ETags for: `private, max-age=0, must-revalidate` for user-scoped data, keeping
it out of shared caches and forcing revalidation before every reuse, and
`public, max-age=0, must-revalidate` for deployment-wide reference data.

### Branch and tag protection

Four rulesets are declared in `.github/branch_protection.yml`:

- **`default`** (every ref): signed commits, and code-quality scanning.
- **`protect-main`**: no branch deletion, no non-fast-forward pushes, signed
  commits, and the required status checks listed in the spec (`CI Pass`,
  `CLI Pass`, `Docker Pass`, the two CodSpeed contexts and `Lighthouse Pass`),
  pinned to the GitHub Actions app by `integration_id`.
- **`protect-main-reviews`**: the `pull_request` rule, so every change reaches
  main through a pull request rather than a direct push, with 1 approving
  review and stale reviews dismissed on push.
- **`protect-release-tags`** (`refs/tags/v*`): restricts tag **creation** and
  **update**. This one is a signing control, not a workflow-hygiene control.
  A `v*` tag may point at any commit in history and GitHub runs a workflow
  from the *tagged* tree, so push access to any branch plus the ability to
  create a tag was enough to resurrect a retired signing workflow at its old
  path and mint a certificate under a SAN the CLI still admits. The
  certificate-extension binding described above cannot distinguish that
  build, because it genuinely is this repository on a GitHub-hosted runner;
  the ruleset is what closes it. Deletion is left unrestricted so
  `release-finalize.yml`'s orphan-dev-tag sweep keeps working, which accepts
  erasure of a published tag as a cost but not forgery, since re-pushing the
  name is a creation.

`scripts/audit_branch_protection.sh` diffs the live rulesets against that
spec: `verify-rulesets.yml` runs it on every PR, and `verify-backend.yml`
runs it again on push to main. The audit strips `bypass_actors` on both
sides, so which actors bypass `protect-release-tags` is deliberately outside
its scope and needs periodic review by hand.

---

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly via
[GitHub Security Advisories](https://github.com/Aureliolo/synthorg/security/advisories/new).
Do not open a public issue for security vulnerabilities.
