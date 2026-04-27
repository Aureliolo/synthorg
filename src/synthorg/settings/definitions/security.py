"""Security namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

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
            " Default 730 (2 years) balances GDPR exposure against"
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
        key="retention_cleanup_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the audit retention purge loop. When"
            " True, the loop stays resident but every tick"
            " short-circuits -- used during incident investigations"
            " to preserve all records."
        ),
        group="Retention",
        level=SettingLevel.ADVANCED,
        yaml_path="security.retention_cleanup_paused",
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
            " 43 URL-safe base64 chars. restart_required because"
            " changing the byte length mid-run silently invalidates"
            " existing tokens (a 32-byte token decoded under a"
            " 64-byte expectation fails verification)."
        ),
        group="Authentication",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=16,
        max_value=64,
        yaml_path="security.auth_token_bytes",
    )
)
