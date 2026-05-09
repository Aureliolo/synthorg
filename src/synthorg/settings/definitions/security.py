"""Security namespace setting definitions."""

from typing import Final

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

# Shared audit-retention-tick bounds + default. Re-exported so
# ``synthorg.security.config.SecurityConfig`` and the Pydantic field
# validators reuse the canonical values instead of duplicating
# numeric literals -- a single source of truth for both startup-model
# validation and live settings registry validation.
AUDIT_RETENTION_TICK_DEFAULT_SECONDS: Final[float] = 86400.0
AUDIT_RETENTION_TICK_MIN_SECONDS: Final[float] = 60.0
AUDIT_RETENTION_TICK_MAX_SECONDS: Final[float] = 604800.0

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Master switch for the security subsystem",
        group="General",
        yaml_path="security.enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="audit_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Whether to record security audit entries",
        group="General",
        yaml_path="security.audit_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="post_tool_scanning_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Scan tool output for secrets and sensitive data",
        group="Output Scanning",
        level=SettingLevel.ADVANCED,
        yaml_path="security.post_tool_scanning_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="output_scan_policy_type",
        type=SettingType.ENUM,
        default="autonomy_tiered",
        description="Response policy when output scan detects sensitive content",
        group="Output Scanning",
        level=SettingLevel.ADVANCED,
        enum_values=("redact", "withhold", "log_only", "autonomy_tiered"),
        yaml_path="security.output_scan_policy_type",
    )
)

# ── Audit retention (CFG-1 audit) ────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="audit_retention_days",
        type=SettingType.INTEGER,
        default="730",
        description=(
            "Number of days to retain audit_entries before automatic"
            " purge. 0 disables purging (unbounded retention)."
            " Default 730 (2 years) balances audit retention against"
            " forensic value."
        ),
        group="Retention",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=36_500,
        yaml_path="security.audit_retention_days",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="audit_retention_loop_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Live kill-switch for the audit retention purge loop. When"
            " ``False`` the loop stays resident but every configured"
            " tick (see ``security.audit_retention_tick_seconds``)"
            " short-circuits -- used during incident investigations"
            " to preserve all records, or to decommission retention"
            " on a deployment that handles it externally."
        ),
        group="Retention",
        level=SettingLevel.ADVANCED,
        yaml_path="security.audit_retention_loop_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="audit_retention_tick_seconds",
        type=SettingType.FLOAT,
        default=str(AUDIT_RETENTION_TICK_DEFAULT_SECONDS),
        description=(
            "Wall-clock interval between audit retention purge ticks."
            " Audit retention is not a hot path; operators tune the"
            " *window* (``security.audit_retention_days``) rather than"
            " the *cadence*. Default 24h. Resolved per-tick by"
            " ``_resolve_audit_retention_tick_seconds``, so operator"
            " changes take effect on the next tick without restart."
        ),
        group="Retention",
        level=SettingLevel.ADVANCED,
        min_value=AUDIT_RETENTION_TICK_MIN_SECONDS,
        max_value=AUDIT_RETENTION_TICK_MAX_SECONDS,
        yaml_path="security.audit_retention_tick_seconds",
    )
)

# ── Auth token entropy budget ────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="auth_token_bytes",
        type=SettingType.INTEGER,
        default="32",
        description=(
            "Entropy budget (bytes) for URL-safe auth-surface tokens"
            " minted by secrets.token_urlsafe: WebSocket tickets,"
            " password-reset tokens, refresh tokens, OAuth state"
            " tokens. 32 bytes resolves to 256 bits of entropy and"
            " 43 URL-safe base64 chars.  Resolves through"
            " DB > env (SYNTHORG_SECURITY_AUTH_TOKEN_BYTES) > YAML"
            " > code default; the DB layer is rejected at write time"
            " because changing the byte length mid-run would silently"
            " invalidate existing tokens (a 32-byte token decoded"
            " under a 64-byte expectation fails verification) --"
            " ``read_only_post_init=True``."
        ),
        group="Authentication",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        read_only_post_init=True,
        min_value=16,
        max_value=64,
        yaml_path="security.auth_token_bytes",
    )
)

# ── Approval-timeout scheduler ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="timeout_check_interval_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Interval at which the approval-timeout scheduler scans for"
            " pending approvals and applies the timeout policy"
            " (approve, deny, or escalate)."
        ),
        group="Timeouts",
        level=SettingLevel.ADVANCED,
        min_value=5.0,
        max_value=600.0,
        yaml_path="security.timeout_check_interval_seconds",
    )
)
