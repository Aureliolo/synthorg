---
title: Security
description: Security architecture, hardening measures, and compliance posture of the SynthOrg framework.
---

# Security

SynthOrg is designed to run autonomous AI agents with real tools and real consequences.
Security is not an afterthought; it is a core architectural concern woven through
every layer of the framework, from the application runtime to the CI/CD pipeline
and container infrastructure.

---

## Application Security

### SecOps Agent & Rule Engine

Every tool invocation passes through a centralized security evaluation pipeline
before execution. The **SecOps service** coordinates a fail-closed rule engine
with five built-in detectors:

| Detector | What It Catches |
|----------|----------------|
| **Policy Validator** | Action type policies (soft-allow / hard-deny / escalate) |
| **Credential Detector** | API keys, passwords, tokens, private keys in arguments or output |
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

### Progressive Trust

Agents start with restricted permissions and earn autonomy over time.
Four pluggable strategies behind the `TrustStrategy` protocol:

1. **Disabled**: all actions require approval regardless of history
2. **Weighted**: accumulate a trust score from successful actions
3. **Per-category**: independent trust tracks per action type (read, write, delete)
4. **Milestone gates**: unlock action categories after specific success thresholds

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
- **Tiered rate limiting**: three budgets surround the auth middleware. **IP floor** (outermost, un-gated) limits *every* request to 10,000 req/min by client IP, including ones the auth middleware rejects with 401; this protects against floods of forged-token traffic on protected endpoints that would otherwise bypass both user-gated tiers. **Unauthenticated** requests (only when ``scope["user"]`` is ``None``) are limited to 20 req/min by client IP (aggressive cap on brute-force against login/setup/logout). **Authenticated** requests (only when ``scope["user"]`` is set) are limited to 6,000 req/min by user ID (generous budget for normal dashboard usage, keyed by user ID so multi-user deployments behind a shared gateway or NAT do not collectively exhaust a single per-IP bucket). The floor default is sized above the authenticated per-user cap (10,000 ≥ 6,000) so a single user on a shared-NAT deployment cannot accidentally clip themselves on the floor before their per-user tier kicks in; config validation rejects a floor below the authenticated cap. The floor is still the aggregate-IP cap, so many active users behind one NAT can collectively exceed it. Raise ``floor_max_requests`` to ``N × auth_max_requests`` for large shared deployments. Auth runs before both user-gated tiers so ``scope["user"]`` is authoritatively populated for the ``check_throttle_handler`` branch; the IP floor runs before auth so invalid-auth floods still hit a rate cap. All three limits are configurable via ``api.rate_limit.floor_max_requests`` / ``unauth_max_requests`` / ``auth_max_requests`` in the YAML config (or ``SYNTHORG_API_RATE_LIMIT_FLOOR_MAX_REQUESTS`` / ``SYNTHORG_API_RATE_LIMIT_UNAUTH_MAX_REQUESTS`` / ``SYNTHORG_API_RATE_LIMIT_AUTH_MAX_REQUESTS`` environment variables for Docker deployments). These are bootstrap-only settings; changes require a restart. The health endpoints (``/api/v1/healthz`` and ``/api/v1/readyz``) are excluded from rate limiting by default via ``rate_limit.exclude_paths``. The WebSocket path is excluded from all three tiers; HTTP-style per-request rate limiting is inappropriate for persistent WebSocket connections. In-memory rate-limit storage is single-replica; multi-replica deployments with shared rate limiting require an external store (not yet supported).

    !!! warning "Migration note"
        The legacy ``max_requests`` field on ``RateLimitConfig`` has been removed.
        Configurations using ``api.rate_limit.max_requests`` are rejected at
        startup with a validation error directing operators to use
        ``unauth_max_requests`` and ``auth_max_requests`` instead.

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
  requests, meetings, users, webhooks, custom_rules, ontology,
  departments, scaling, connections, personalities, reviews,
  artifacts, backup, oauth, settings, setup, simulations, training,
  escalations, a2a, and auth ws-ticket.
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

### Notification Security

Notification adapter configuration may contain credentials (SMTP passwords,
ntfy tokens, Slack webhook URLs). These values are stored in the ``params``
dict of each ``NotificationSinkConfig`` entry in the YAML config.

- **Credentials in params**: Treat ``password``, ``token``, and ``webhook_url``
  params as sensitive. Use environment variable substitution in YAML
  (``${SMTP_PASSWORD}``) rather than embedding plain-text secrets.
- **Log redaction**: The observability pipeline's ``sanitize_sensitive_fields``
  processor automatically redacts keys matching ``password``, ``token``, and
  ``secret`` at all nesting depths, so adapter params are not leaked in logs.
- **Transport security**: The email adapter enforces STARTTLS when
  ``use_tls=true`` (default). The ntfy and Slack adapters validate that their
  target URLs use HTTPS before sending (SSRF-safe: private/loopback IPs are
  rejected).

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
| `Cache-Control` | `no-store` (API); `no-cache` (dashboard HTML); `public, max-age=31536000, immutable` (dashboard hashed assets); `public, max-age=300` (docs) |
| `Content-Security-Policy` | Strict default; dashboard uses CSP Level 3 directive splitting: `style-src-elem 'self' 'nonce-...'` locks `<style>` elements to the per-request nonce, `style-src-attr 'unsafe-inline'` covers the transient inline positioning styles set by Floating UI. `script-src 'self'` with no `'unsafe-inline'`. See [CSP Nonce Infrastructure](#csp-nonce-infrastructure). Docs UI location has its own relaxed CSP (inline syntax-highlighting requirement of the Material theme). |

---

## Container Hardening

### Distroless Runtime

The backend runs on a **Wolfi-based, apko-composed distroless Python image**: no
shell, no package manager, minimal attack surface. The build uses a 2-stage Dockerfile:

1. **Builder**: compiles dependencies via uv, fixes venv symlink for Wolfi's Python path
2. **Runtime**: apko-composed Wolfi base (no shell, UID 65532)

Base images are declared in `docker/*/apko.yaml` with relaxed package specs
(e.g. `python-3.14`). Exact patch versions are resolved and pinned by
`docker/*/apko.lock.json`, refreshed weekly by `.github/workflows/apko-lock.yml`.

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
- **apko lockfiles** (`docker/*/apko.lock.json`) reconciled weekly by `.github/workflows/apko-lock.yml`
- **Renovate** auto-updates digests daily for the thin backend and sandbox Dockerfiles
- **cosign keyless signing** on every pushed image (Sigstore OIDC-bound)
- **Buildx SPDX SBOMs** (SLSA L1) auto-generated and pushed to GHCR as registry attestations (inspect via `docker buildx imagetools inspect`). Standalone CycloneDX JSON SBOMs are generated separately by Syft. See [Software Bill of Materials](#software-bill-of-materials-sbom) below.
- **Build-level provenance** (SLSA L1) auto-generated by Docker Buildx
- **SLSA Level 3 provenance** for CLI binary releases and container images (generated by `actions/attest-build-provenance`, Sigstore-signed, independently verifiable)
- **Client-side verification**: The CLI (`synthorg start`, `synthorg update`) automatically verifies cosign signatures and SLSA provenance for container images before pulling. Verified digests are pinned in the compose file to prevent tag mutation attacks. Bypass with `--skip-verify` or `SYNTHORG_SKIP_VERIFY=1` for air-gapped environments (not recommended).

---

## Supply Chain Security

### Dependency Management

| Layer | Tool | Policy |
|-------|------|--------|
| Python | `pip-audit` | Per-PR + weekly scan for known CVEs |
| Python | Renovate | Daily updates, `==` pinned versions, grouped by domain |
| Node.js | `npm audit` | Per-PR, blocks on critical/high |
| Node.js | Renovate | Daily updates via lockfile (`/web`, `/site`, `/.github`) |
| GitHub Actions | Renovate | Daily updates, pinned by commit SHA |
| Pre-commit hooks | Renovate | Daily updates, version-pinned `rev:` tags |
| CI binary tools | Renovate | Daily updates via regex managers (Trivy, Gitleaks, D2, apko) |
| License | `dependency-review-action` | Permissive-only allowlist (MIT, Apache-2.0, BSD, ISC, etc.) |
| Supply chain | Socket.dev | GitHub App; detects typosquatting, malware, suspicious ownership changes |

### Container Scanning

Every container image is scanned before push:

- **Trivy**: CRITICAL = hard fail, HIGH = warn-only (`.trivyignore.yaml` for vetted CVEs)
- **CIS Docker Benchmark**: `trivy image --compliance docker-cis-1.6.0` run against all images (enforced; any FAIL blocks the build)

Images are **only pushed to GHCR after vulnerability scans and CIS benchmark pass**.

### Signed Artifacts

- **Container images**: cosign keyless signatures (verify via `cosign verify`) + SLSA Level 3 provenance attestations (verify via `gh attestation verify`)
- **CLI binaries**:
    - cosign keyless signature on checksums file (verify via `cosign verify-blob`)
    - SLSA Level 3 provenance attestations (verify via `gh attestation verify`)
    - Sigstore provenance bundle (`.sigstore.json`, verify via `cosign verify-blob-attestation`)
- **Git commits**: GPG/SSH signed (enforced by branch protection ruleset)
- **GitHub Actions**: All actions pinned by full SHA commit hash
- **GitHub Releases**: Immutable releases enabled; once published, assets and body cannot be modified (prevents supply chain tampering). Releases are created as drafts by Release Please, finalized after all assets are attached.

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
| Base64 Disclosure | 10094 | Ignore | OpenAPI schema contains UUID/JWT-format refs, not secrets |
| Sec-Fetch-* Missing | 90005 | Ignore | CSRF is mitigated via the double-submit cookie pattern; Sec-Fetch-* headers are defence-in-depth but not required, and enforcing them would break non-browser API clients |
| Non-Storable Content | 10049 | Warn | API endpoints correctly use `no-store`; dashboard HTML uses `no-cache`; hashed assets use `immutable`; docs use `public, max-age=300` |
| Application Error Disclosure | 90022 | Ignore | SynthOrg's RFC 9457 / ProblemDetail envelopes set `ErrorCategory.INTERNAL` with the title `"Internal Server Error"` (see `category_title` in `src/synthorg/core/error_taxonomy.py`), so the rule's substring match will trigger for our legitimate structured 5xx responses. Regression coverage for actual debug-page leaks lives in the exception-handler unit tests. |
| Debug Error Messages | 10023 | Ignore | Same trigger and rationale as 90022. |
| Cookie No HttpOnly Flag | 10010 | Ignore | The `csrf_token` cookie is intentionally configured non-HttpOnly (`httponly=False` in `src/synthorg/api/auth/cookies.py`) as part of the double-submit CSRF pattern: the frontend reads the cookie and echoes its value back in the `X-CSRF-Token` header. Suppressing the rule prevents recurring noise on this intentional configuration. The auth/session cookie itself is HttpOnly. |
| Authentication Request Identified | 10111 | Ignore | Under the current ZAP ruleset, this rule labels endpoints carrying a `password` field for the scanner's own auth-flow inference; not a security finding. |
| Sensitive Information in URL | 10024 | Ignore | Under the current ZAP ruleset matching behavior, this rule fires when a URL contains substrings such as `session`. In SynthOrg, `session_id` query parameters reference domain runtime/agent session resources (a workflow ID), not HTTP/auth session tokens; auth state is carried in HttpOnly cookies. Behavior can vary across ZAP versions, so revisit on upgrade. |

The rules file is reviewed when ZAP or the API surface changes.
**When upgrading the ZAP action, the bundled ZAP version, or the
ruleset it uses,** revisit each Ignore row above to confirm the
underlying rule's matcher behavior, severity, and rule ID have not
changed.  Action wrapper bumps and ZAP-engine bumps both alter what
each rule fires on; do not skip the revisit just because the action
version changed by a single minor bump.
Cache-Control is path-aware: API data endpoints use `no-store` to prevent
sensitive data caching, the web dashboard entry point (`index.html`) uses
`no-cache` to force revalidation on every request (ensuring fresh deployments),
content-hashed dashboard assets (`/assets/*`) use `public, max-age=31536000,
immutable` for long-lived caching, and documentation endpoints (`/docs/*`)
allow brief client and proxy caching since they serve public, non-user-specific
content.

### Branch Protection

The `protect-main` ruleset enforces:

- **Signed commits** required
- **No direct pushes**: all changes via pull request
- **1 approving review** required (stale reviews dismissed on push)
- **`ci-pass` status check** required before merge
- **No branch deletion** or non-fast-forward pushes

---

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly via
[GitHub Security Advisories](https://github.com/Aureliolo/synthorg/security/advisories/new).
Do not open a public issue for security vulnerabilities.
