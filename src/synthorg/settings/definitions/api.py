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

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

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
        description="Path to SSL certificate file (PEM format)",
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        yaml_path="api.server.ssl_certfile",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="ssl_keyfile",
        type=SettingType.STRING,
        default="",
        description="Path to SSL private key file (PEM format)",
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
        description="Path to CA bundle for client certificate verification",
        group="Server",
        level=SettingLevel.ADVANCED,
        restart_required=True,
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
