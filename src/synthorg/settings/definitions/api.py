"""API namespace setting definitions.

Registers settings covering server, TLS, CORS, rate limiting (global
+ per-operation sliding-window + per-operation inflight),
authentication, setup, and the WebSocket frame-receive / revalidation
budget added in the #1683 reliability bundle.

Counts (kept generic on purpose; the registry below is the
authoritative source so docstring counts do not silently drift on the
next addition):

* The majority are ``restart_required=True`` because Litestar bakes
  middleware, rate-limit budgets, CORS origins, store backends, and
  WebSocket frame-timeout / revalidation tracker construction into the
  application at construction time.
* The remainder are runtime-editable and picked up by the matching
  ``SettingsSubscriber`` on change.
* A subset of the restart-required entries also carry
  ``read_only_post_init=True`` for surfaces that are init-only at the
  controller construction site (currently the WebSocket budget knobs).
"""

from typing import Final

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()


# ── Per-operation rate-limit / inflight defaults ────────────────
# Module-level defaults exported as ``Final[int]`` so the per-op
# policy registry in ``synthorg.api.rate_limits.policies`` imports
# them without re-introducing bare numeric literals in business
# logic.  ``settings/definitions/`` is allowlisted by the
# no-magic-numbers gate, which is the canonical home for every
# numeric tuning knob in the codebase.  Operator runtime tuning for
# these values flows through the registered ``per_op_rate_limit
# _overrides`` and ``per_op_concurrency_overrides`` settings further
# down this file (the registries below are the typed defaults a
# fresh deployment ships with).

# Sliding-window rate limit on the SSE event stream:
# (max_requests_per_window, window_seconds).
EVENTS_STREAM_RATE_LIMIT_MAX_REQUESTS: Final[int] = 60
EVENTS_STREAM_RATE_LIMIT_WINDOW_SECONDS: Final[int] = 60

# Inflight (concurrent requests per subject) caps for the small set
# of long-running / GPU-intensive endpoints that need a hard ceiling
# beyond the sliding-window rate limit.  Single-slot caps protect
# operations that mutate global state (memory checkpoints, fine-tune
# runs) from concurrent-modification races; the dual-slot caps on
# provider discovery / pull throttle outbound network fan-out
# without serialising the user.
EVENTS_STREAM_INFLIGHT_MAX: Final[int] = 4
MEMORY_CHECKPOINT_DEPLOY_INFLIGHT_MAX: Final[int] = 1
MEMORY_CHECKPOINT_ROLLBACK_INFLIGHT_MAX: Final[int] = 1
MEMORY_FINE_TUNE_INFLIGHT_MAX: Final[int] = 1
PROVIDERS_DISCOVER_MODELS_INFLIGHT_MAX: Final[int] = 2
PROVIDERS_PULL_MODEL_INFLIGHT_MAX: Final[int] = 2

# ── Server (bootstrap-only) ──────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="server_host",
        type=SettingType.STRING,
        default="127.0.0.1",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Server bind"
            " address."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.server.host",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="server_port",
        type=SettingType.INTEGER,
        default="3001",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Server bind"
            " port."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=1,
        max_value=65535,
        yaml_path="api.server.port",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="api_prefix",
        type=SettingType.STRING,
        default="/api/v1",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] URL prefix for"
            " all API routes."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.api_prefix",
    )
)

# ── TLS (bootstrap-only) ────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ssl_certfile",
        type=SettingType.STRING,
        default="",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Path to SSL"
            " certificate file (PEM format)."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.server.ssl_certfile",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ssl_keyfile",
        type=SettingType.STRING,
        default="",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Path to SSL"
            " private key file (PEM format)."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        sensitive=True,
        yaml_path="api.server.ssl_keyfile",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ssl_ca_certs",
        type=SettingType.STRING,
        default="",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Path to CA"
            " bundle for client certificate verification."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.server.ssl_ca_certs",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="trusted_proxies",
        type=SettingType.JSON,
        default="[]",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] IP addresses /"
            " CIDRs trusted as reverse proxies for X-Forwarded-For /"
            " X-Forwarded-Proto header processing."
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.server.trusted_proxies",
    )
)

# ── Documentation CSP origins ───────────────────────────────────
# Trusted external origins permitted by the relaxed Content-Security-
# Policy on /docs/ paths (Scalar UI loads JS, fonts, and an API proxy
# from these). Operators who mirror Scalar UI assets to an internal
# CDN override this list; the resolved value is applied uniformly to
# script-src, style-src, img-src, font-src, and connect-src.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="csp_docs_external_origins",
        type=SettingType.JSON,
        default=(
            '["https://cdn.jsdelivr.net","https://fonts.scalar.com",'
            '"https://proxy.scalar.com"]'
        ),
        description=(
            "External origins trusted by the relaxed Content-Security-"
            "Policy on /docs/ paths. Defaults to the Scalar UI public"
            " CDN, fonts, and proxy hosts. Override (for example, to an"
            " internally-mirrored CDN) when operators do not allow the"
            " backend to reach the public Scalar infrastructure."
        ),
        group="Security Headers",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        yaml_path="api.csp.docs_external_origins",
    )
)

# ── Error documentation base URL ────────────────────────────────
# RFC 9457 problem-detail responses build their ``type`` URI from this
# base. Operators who fork or mirror the public docs site override
# this so deep links resolve into their copy.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="error_docs_base_url",
        type=SettingType.STRING,
        default="https://synthorg.io/docs/errors",
        description=(
            "Base URL used to build the RFC 9457 ``type`` field on every"
            " API error response. Each error category appends a fragment"
            " anchor (for example ``#auth``). Override when the docs"
            " site is hosted at a non-default origin. HTTPS-only: the"
            " URL appears in every error response and must not downgrade"
            " operator deployments to plaintext."
        ),
        group="Errors",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=(r"^https://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:/[^\s?#]*)?$"),
        yaml_path="api.error_docs_base_url",
    )
)

# ── CORS (bootstrap-only) ────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="cors_allowed_origins",
        type=SettingType.JSON,
        default="[]",
        description=(
            "[Bootstrap-only -- read via RootConfig at startup; this entry"
            " exists for /settings discoverability only.] Origins permitted"
            " to make cross-origin requests.  Empty default denies all"
            " cross-origin requests; operators must explicitly allowlist"
            " dashboard origins (e.g. ``http://localhost:5173`` for local"
            " development). Matches CorsConfig default."
        ),
        group="CORS",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        yaml_path="api.cors.allowed_origins",
    )
)

# ── Rate Limiting (exclude_paths: bootstrap-only) ────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_unauth_max_requests",
        type=SettingType.INTEGER,
        default="20",
        description="Maximum unauthenticated requests per time window (by IP)",
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=10000,
        yaml_path="api.rate_limit.unauth_max_requests",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_auth_max_requests",
        type=SettingType.INTEGER,
        default="6000",
        description="Maximum authenticated requests per time window (by user ID)",
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=100000,
        yaml_path="api.rate_limit.auth_max_requests",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_time_unit",
        type=SettingType.ENUM,
        default="minute",
        description="Rate limit time window",
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        enum_values=("second", "minute", "hour", "day"),
        yaml_path="api.rate_limit.time_unit",
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_exclude_paths",
        type=SettingType.JSON,
        default='["/api/v1/healthz", "/api/v1/readyz"]',
        description="Paths excluded from rate limiting",
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        yaml_path="api.rate_limit.exclude_paths",
    )
)

# ── Authentication (exclude_paths: bootstrap-only) ───────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="jwt_expiry_minutes",
        type=SettingType.INTEGER,
        default="1440",
        description="JWT token lifetime in minutes",
        group="Authentication",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10080,
        yaml_path="api.auth.jwt_expiry_minutes",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="min_password_length",
        type=SettingType.INTEGER,
        default="12",
        description="Minimum password length for setup and password change",
        group="Authentication",
        min_value=12,
        max_value=128,
        yaml_path="api.auth.min_password_length",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="auth_exclude_paths",
        type=SettingType.JSON,
        default="[]",
        description="Paths excluded from authentication middleware",
        group="Authentication",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        yaml_path="api.auth.exclude_paths",
    )
)

# ── Setup ──────────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="setup_complete",
        type=SettingType.BOOLEAN,
        default="false",
        description="Whether first-run setup has been completed",
        group="Setup",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="setup_has_gpu",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Whether the host running the dashboard has a GPU."
            " Flipped by the setup wizard (or an operator) and read"
            " at setup-completion time to steer embedding-model tier"
            " inference.  No platform probe today -- operator opts"
            " in explicitly."
        ),
        group="Setup",
        level=SettingLevel.ADVANCED,
        yaml_path="api.setup.has_gpu",
    )
)

# ── Ticket cleanup / request size / compression ──────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ticket_cleanup_interval_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=("Interval between WebSocket ticket-store cleanup sweeps"),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=3600.0,
        yaml_path="api.ticket_cleanup_interval_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="per_op_rate_limit_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for per-operation sliding-window rate limits"
            ". Disable to make all per_op_rate_limit guards no-ops."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        yaml_path="api.per_op_rate_limit.enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="per_op_rate_limit_overrides",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Per-operation sliding-window overrides keyed by operation"
            ' name, e.g. {"memory.fine_tune": [2, 3600]}. Each value'
            " is a 2-tuple of [max_requests, window_seconds]."
            " Setting either component to 0 disables that operation's"
            " guard. Runtime-editable -- changes take effect on the"
            " next request, no restart required."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        yaml_path="api.per_op_rate_limit.overrides",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="per_op_concurrency_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for per-operation inflight-concurrency caps"
            ". Disable to make the PerOpConcurrencyMiddleware"
            " a no-op for all requests."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        yaml_path="api.per_op_concurrency.enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="per_op_concurrency_overrides",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Per-operation inflight overrides keyed by operation name,"
            ' e.g. {"memory.fine_tune": 1}. Value is max_inflight'
            " (positive integer). Setting a value to 0 disables the"
            " operation's inflight guard. Runtime-editable -- changes"
            " take effect on the next request, no restart required."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        yaml_path="api.per_op_concurrency.overrides",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="max_rpm_default",
        type=SettingType.INTEGER,
        default="60",
        description=(
            "Fallback max requests-per-minute applied to per-connection"
            " coordinators when the catalog does not provide a value"
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=100_000,
        yaml_path="api.rate_limit.max_rpm_default",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="compression_minimum_size_bytes",
        type=SettingType.INTEGER,
        default="1000",
        description=(
            "Minimum response body size in bytes before brotli compression is applied"
        ),
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=100,
        max_value=10_000,
        yaml_path="api.server.compression_minimum_size_bytes",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="request_max_body_size_bytes",
        type=SettingType.INTEGER,
        default="52428800",
        description="Maximum accepted HTTP request body size in bytes",
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1_000_000,
        max_value=536_870_912,
        yaml_path="api.server.request_max_body_size_bytes",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ws_ticket_max_pending_per_user",
        type=SettingType.INTEGER,
        default="5",
        description=("Maximum pending WebSocket auth tickets allowed per user"),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
        yaml_path="api.ws_ticket_max_pending_per_user",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="sse_keepalive_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Idle interval after which the AG-UI SSE stream emits a"
            " keepalive frame so intermediaries (load balancers, proxies)"
            " do not close the connection. Resolved once per stream open;"
            " a runtime change applies to subsequent streams."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=600.0,
        yaml_path="api.sse_keepalive_seconds",
    )
)

# ── Query limits (controller clamps) ─────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="max_lifecycle_events_per_query",
        type=SettingType.INTEGER,
        default="10000",
        description=(
            "Maximum lifecycle events returned by the activities endpoint"
            " for a single query"
        ),
        group="Query Limits",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=1_000_000,
        yaml_path="api.query_limits.max_lifecycle_events",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="max_audit_records_per_query",
        type=SettingType.INTEGER,
        default="10000",
        description=(
            "Maximum audit records returned by the audit endpoint for a single query"
        ),
        group="Query Limits",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=1_000_000,
        yaml_path="api.query_limits.max_audit_records",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="max_metrics_per_query",
        type=SettingType.INTEGER,
        default="10000",
        description=(
            "Maximum metrics records returned by the coordination metrics"
            " endpoint for a single query"
        ),
        group="Query Limits",
        level=SettingLevel.ADVANCED,
        min_value=100,
        max_value=1_000_000,
        yaml_path="api.query_limits.max_metrics",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="max_meeting_context_keys",
        type=SettingType.INTEGER,
        default="20",
        description=(
            "Maximum number of context keys attached to a single meeting"
            " (baked into the request DTO validator at startup)"
        ),
        group="Query Limits",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=5,
        max_value=100,
        yaml_path="api.query_limits.max_meeting_context_keys",
    )
)

# ── CFG-1 audit: cache, WS auth, cleanup, urgency ────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ws_auth_timeout_seconds",
        type=SettingType.FLOAT,
        default="10.0",
        description=(
            "How long the WebSocket handler waits for the first-message"
            " auth payload after accepting the connection before"
            " closing with a 4001 auth-timeout code."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=120.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ws_frame_timeout_seconds",
        type=SettingType.INTEGER,
        default="30",
        description=(
            "Per-frame receive timeout for established WebSocket"
            " connections. A connection that goes idle (no inbound"
            " frame) for longer than this is closed with policy code"
            " 1008. Bounds the number of slots a silent client can"
            " hold (DoS prevention). Resolved at controller"
            " construction; runtime mutation requires a restart."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=1,
        max_value=600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ws_revalidation_window_seconds",
        type=SettingType.INTEGER,
        default="60",
        description=(
            "Sliding-window length (seconds) for WebSocket session"
            " revalidation failures. Persistence backend errors are"
            " admitted into a per-connection sliding window of this"
            " length; once the window saturates the connection is"
            " closed. Replaces the legacy reset-on-success counter"
            " so a flaky persistence layer cannot indefinitely keep"
            " a connection alive by interleaving successes."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=1,
        max_value=3_600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ws_revalidation_max_failures",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Maximum number of revalidation failures admitted in the"
            " ws_revalidation_window_seconds window before the"
            " WebSocket is closed with server-error code 4011 so the"
            " client reconnects against a healthy replica."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_cleanup_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for the WS ticket / session / lockout"
            " cleanup loop. When False the loop stays resident but"
            " every tick short-circuits -- pauses cleanup without"
            " tearing down lifecycle."
        ),
        group="WebSocket",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limiter_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for the three-tier global rate"
            " limiter (IP floor + unauthenticated + authenticated)."
            " Disable only in trusted dev environments.  Resolves"
            " through DB > env (SYNTHORG_API_RATE_LIMITER_ENABLED)"
            " > YAML > code default; the DB layer is rejected at"
            " write time because the middleware stack is baked at"
            " app construction (read_only_post_init=True)."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="approval_urgency_critical_seconds",
        type=SettingType.FLOAT,
        default="3600.0",
        description=(
            "Time-remaining threshold at or below which a pending"
            " approval is classified 'critical' (default 1 hour)."
            " Must be less than approval_urgency_high_seconds."
        ),
        group="Approvals",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=60.0,
        max_value=86_400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="approval_urgency_high_seconds",
        type=SettingType.FLOAT,
        default="14400.0",
        description=(
            "Time-remaining threshold at or below which a pending"
            " approval is classified 'high' (default 4 hours)."
        ),
        group="Approvals",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=300.0,
        max_value=604_800.0,
    )
)

# ── Sliding-window rate-limiter GC tuning ───────────────────────
# Fallback module constants in api/rate_limits/in_memory*.py mirror
# these defaults so a limiter constructed without a settings service
# (test harness, anonymous boot path) still observes the documented
# GC cadence.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_gc_every_n_acquires",
        type=SettingType.INTEGER,
        default="1024",
        description=(
            "Sliding-window limiter: number of acquires between"
            " cold-bucket GC sweeps. Higher values reduce sweep"
            " frequency at the cost of slower stale-bucket eviction."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=64,
        max_value=65_536,
        yaml_path="api.rate_limit.gc_every_n_acquires",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_gc_min_horizon_seconds",
        type=SettingType.INTEGER,
        default="60",
        description=(
            "Sliding-window limiter: minimum horizon (seconds) for"
            " evicting cold buckets. Caps the window-derived eviction"
            " floor so fast-rotating buckets are not held forever."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=3600,
        yaml_path="api.rate_limit.gc_min_horizon_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_inflight_gc_every_n_acquires",
        type=SettingType.INTEGER,
        default="1024",
        description=(
            "Inflight (per-op concurrency) limiter: number of acquires"
            " between cold-bucket GC sweeps."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=64,
        max_value=65_536,
        yaml_path="api.rate_limit.inflight_gc_every_n_acquires",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_inflight_min_retry_after_seconds",
        type=SettingType.INTEGER,
        default="1",
        description=(
            "Inflight (per-op concurrency) limiter: floor on the"
            " ``Retry-After`` header value emitted on 429 responses."
        ),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=300,
        yaml_path="api.rate_limit.inflight_min_retry_after_seconds",
    )
)

# ── Lifecycle shutdown stage budgets ────────────────────────────
# Per-stage soft deadlines for the graceful shutdown sequence.
# Fallback module constants in api/lifecycle.py mirror these defaults.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_task_engine_shutdown_seconds",
        type=SettingType.FLOAT,
        default="8.0",
        description=(
            "Lifecycle shutdown: soft deadline for the task engine"
            " stop step. Beyond this, in-flight tasks are cancelled."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=120.0,
        yaml_path="api.lifecycle.task_engine_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_meeting_scheduler_shutdown_seconds",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Lifecycle shutdown: soft deadline for the meeting scheduler stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.meeting_scheduler_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_performance_tracker_shutdown_seconds",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Lifecycle shutdown: soft deadline for the performance tracker stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.performance_tracker_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_backup_shutdown_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Lifecycle shutdown: soft deadline for the backup service"
            " stop step. Allows in-flight archive flushes to complete."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.backup_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_settings_dispatcher_shutdown_seconds",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Lifecycle shutdown: soft deadline for the settings dispatcher stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.settings_dispatcher_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_bridge_shutdown_seconds",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Lifecycle shutdown: soft deadline for the bus / webhook"
            " bridge stop step (per bridge)."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.bridge_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_distributed_queue_shutdown_seconds",
        type=SettingType.FLOAT,
        default="3.0",
        description=(
            "Lifecycle shutdown: soft deadline for the distributed queue stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.distributed_queue_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_message_bus_shutdown_seconds",
        type=SettingType.FLOAT,
        default="3.0",
        description=(
            "Lifecycle shutdown: soft deadline for the in-process"
            " message bus stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.message_bus_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_persistence_shutdown_seconds",
        type=SettingType.FLOAT,
        default="5.0",
        description=(
            "Lifecycle shutdown: soft deadline for the persistence"
            " backend stop step (connection pool drain + checkpoint)."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=1.0,
        max_value=120.0,
        yaml_path="api.lifecycle.persistence_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_approval_timeout_shutdown_seconds",
        type=SettingType.FLOAT,
        default="1.0",
        description=(
            "Lifecycle shutdown: soft deadline for the approval"
            " timeout scheduler stop step."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=60.0,
        yaml_path="api.lifecycle.approval_timeout_shutdown_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="lifecycle_drain_timeout_seconds",
        type=SettingType.FLOAT,
        default="40.0",
        description=(
            "Lifecycle shutdown: hard deadline on the cumulative"
            " stop sequence. Acts as the outer ``asyncio.wait_for``"
            " budget covering every per-stage step. Default leaves"
            " headroom above the current cumulative per-stage soft"
            " deadlines so the outer budget does not pre-empt a"
            " normally progressing stage."
        ),
        group="Lifecycle Shutdown",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
        yaml_path="api.lifecycle.drain_timeout_seconds",
    )
)
