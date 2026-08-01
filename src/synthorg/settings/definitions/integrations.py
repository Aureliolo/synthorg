"""Integrations namespace setting definitions.

Covers health probing of external connections, OAuth flow HTTP
timeouts, and rate-limit coordinator polling.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="health_probe_interval_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=("How often the background prober checks integration health"),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=3600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_http_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "HTTP timeout for OAuth token exchange across device,"
            " authorization-code, and client-credentials flows."
            " Resolved per OAuth operation (callback exchange and each"
            " background token refresh), so a change applies without a"
            " restart."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=300.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_token_check_interval_seconds",
        type=SettingType.INTEGER,
        default="60",
        description=(
            "How often the OAuth token manager sweeps connections for"
            " tokens nearing expiry. Lower values refresh sooner at the"
            " cost of more frequent catalog scans."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=5,
        max_value=3600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_token_refresh_threshold_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "Refresh OAuth tokens that expire within this many seconds."
            " Must exceed the check interval so a token is refreshed"
            " before it lapses."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=86400,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_device_flow_max_wait_seconds",
        type=SettingType.INTEGER,
        default="600",
        description=(
            "Maximum time to poll the OAuth device-flow token endpoint before giving up"
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=7200,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="rate_limit_coordinator_poll_timeout_seconds",
        type=SettingType.FLOAT,
        default="0.5",
        description=("Poll timeout for the shared-state rate-limit coordinator"),
        group="Rate Limiting",
        level=SettingLevel.ADVANCED,
        min_value=0.1,
        max_value=10.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="webhook_receipt_retention_days",
        type=SettingType.INTEGER,
        default="0",
        description=(
            "Default retention window for webhook receipts (days)."
            " 0 (the default) never sweeps, so nothing is discarded unless an"
            " operator asks for a window. Per-connection overrides on the"
            " connection itself take precedence when set. No code path writes"
            " a receipt yet, so this currently has nothing to sweep."
        ),
        group="Webhooks",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=36_500,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="secret_capture_ttl_seconds",
        type=SettingType.INTEGER,
        default="600",
        description=(
            "Lifetime (seconds) of an out-of-band secret-capture handle used"
            " by the conversational setup flow before it expires and is swept."
            " Kept short so a captured credential lives only long enough to be"
            " consumed by connections.create."
        ),
        group="Connections",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=3_600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="health_healthy_recheck_seconds",
        type=SettingType.INTEGER,
        default="21600",
        description=(
            "How long a healthy connection's verdict is trusted before it is"
            " probed again. Long on purpose: a probe against a metered"
            " third-party API costs real quota, and re-proving a working"
            " credential every few minutes spends money to change nothing."
        ),
        group="Connections",
        level=SettingLevel.ADVANCED,
        min_value=60,
        max_value=604_800,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="health_degraded_recheck_seconds",
        type=SettingType.INTEGER,
        default="1800",
        description=(
            "Recheck interval for a connection that has started failing but"
            " has not been written off yet."
        ),
        group="Connections",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=604_800,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="health_unhealthy_recheck_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "Recheck interval for a failed connection, where the operator is"
            " waiting to see it recover. Shortest of the three: this is the"
            " state someone is actively watching."
        ),
        group="Connections",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=604_800,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="webhook_receipt_cleanup_tick_seconds",
        type=SettingType.FLOAT,
        default="86400.0",
        description=(
            "Wall-clock interval between webhook-receipt sweep ticks."
            " Receipts are retained in days; a daily sweep is the right"
            " granularity. Operators tune the *window*"
            " (``integrations.webhooks.receipt_retention_days`` or the"
            " per-connection override) rather than the *cadence*."
            " Default 24h. Resolved per-tick by"
            " ``_resolve_webhook_receipt_cleanup_tick_seconds``, so"
            " operator changes take effect on the next tick without"
            " restart."
        ),
        group="Webhooks",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=604800.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_device_flow_poll_interval_seconds",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "Initial polling interval for the OAuth device-code flow."
            " The IETF spec lets the server widen this dynamically with"
            " a slow_down response (the dynamic widening is preserved);"
            " this setting controls only the starting cadence."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=60,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="oauth_idempotency_retention_seconds",
        type=SettingType.FLOAT,
        default="600.0",
        description=(
            "Retention window (seconds) for consumed OAuth state rows."
            " Consumed rows older than this are reaped by the periodic"
            " cleanup task. Sized to absorb realistic IdP redelivery"
            " envelopes (provider retries, browser back/forward, CDN"
            " replays) without growing the table indefinitely."
        ),
        group="OAuth",
        level=SettingLevel.ADVANCED,
        min_value=60.0,
        max_value=86_400.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="tunnel_provider",
        type=SettingType.ENUM,
        default="cloudflare",
        enum_values=("cloudflare", "ngrok", "devtunnels"),
        description=(
            "Which provider exposes the local API on a public URL for"
            " webhook development. 'cloudflare' runs an accountless"
            " quick tunnel (default); 'ngrok' needs an auth token"
            " (paste it on the tunnel card); 'devtunnels' needs the"
            " devtunnel CLI plus a GitHub device-code login. Resolved"
            " fresh at every tunnel start, so a change applies without"
            " a restart."
        ),
        group="Tunnel",
    )
)

_r.register(
    # manager's state-dir paths (downloaded binaries + the devtunnel CLI's
    # confined HOME); the adapters cache these at construction, so a mid-run
    # DB write would silently drift from the directories actually in use.
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="tunnel_state_dir",
        type=SettingType.STRING,
        default="",
        description=(
            "Root directory for tunnel runtime state (downloaded provider"
            " binaries under bin/, the devtunnel CLI's confined login home"
            " under devtunnels-home/). Sourced from the"
            " SYNTHORG_TUNNEL_STATE_DIR env var at process start; the"
            " CLI-generated compose sets /data/tunnel so state survives"
            " container recreation. Empty means the bare-metal default"
            " ~/.synthorg."
        ),
        group="Tunnel",
        level=SettingLevel.ADVANCED,
        compose_set=True,
        env_var_override="SYNTHORG_TUNNEL_STATE_DIR",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.INTEGRATIONS,
        key="github_api_url",
        type=SettingType.STRING,
        default="https://api.github.com",
        description=(
            "GitHub API base URL (HTTPS only).  Override for GitHub"
            " Enterprise installations (e.g."
            " ``https://github.example.com/api/v3``) or self-hosted"
            " GitHub-compatible services."
        ),
        group="GitHub",
        level=SettingLevel.ADVANCED,
        # HTTPS-only: a bearer token rides the Authorization header, so a
        # plaintext http:// endpoint would leak it on the wire.
        validator_pattern=r"^https://[\w.\-:]+(?:/.*)?$",
    )
)
