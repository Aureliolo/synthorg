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

# Red-team gate per-evaluation timeout bounds. Shared so the Pydantic
# field validator on ``RedTeamConfig.timeout_seconds`` and any future
# settings-registry entry resolve from the same literal source.
RED_TEAM_TIMEOUT_DEFAULT_SECONDS: Final[float] = 60.0
RED_TEAM_TIMEOUT_MAX_SECONDS: Final[float] = 600.0

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for the security subsystem. A change is applied"
            " to the live per-request interceptor via a settings subscriber"
            " without a restart. Disabling (true->false) is a"
            " security-weakening transition and requires the deliberate"
            " confirm+reason+actor guardrail at the write path."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="audit_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether to record security audit entries. Applied to the live"
            " interceptor via a settings subscriber without a restart."
            " Disabling (true->false) is a security-weakening transition and"
            " requires the deliberate confirm+reason+actor guardrail."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="post_tool_scanning_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Scan tool output for secrets and sensitive data. Applied to"
            " the live interceptor via a settings subscriber without a"
            " restart. Disabling (true->false) is a security-weakening"
            " transition and requires the deliberate confirm+reason+actor guardrail."
        ),
        group="Output Scanning",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="tls_ca_bundle",
        type=SettingType.STRING,
        default="",
        description=(
            "Path to an additional CA bundle trusted by every outbound call:"
            " the git subprocesses (workspace backends, docs engine, agent git"
            " tools) and the httpx clients (forge, chat, deploy, health, A2A)."
            " Additional, not replacing, so naming a private CA does not stop"
            " the public roots being trusted. Blank uses the system trust store"
            " alone. Read live per call, so a change applies to the next one"
            " without a restart."
        ),
        group="TLS Trust",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="tls_verify",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether outbound TLS certificates are verified at all, across"
            " both the git and httpx transports. It exists because a"
            " self-signed host is a real situation an operator will otherwise"
            " work around with something worse, but turning it off"
            " (true->false) trusts any certificate presented to the product"
            " and is a security-weakening transition requiring the deliberate"
            " confirm+reason+actor guardrail. Prefer tls_ca_bundle."
        ),
        group="TLS Trust",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="output_scan_policy_type",
        type=SettingType.ENUM,
        default="autonomy_tiered",
        description=(
            "Response policy when output scan detects sensitive content."
            " Applied to the live interceptor via a settings subscriber"
            " without a restart. Switching to ``log_only`` is a"
            " security-weakening transition and requires the deliberate"
            " confirm+reason+actor guardrail."
        ),
        group="Output Scanning",
        level=SettingLevel.ADVANCED,
        enum_values=("redact", "withhold", "log_only", "autonomy_tiered"),
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
            " > code default. Governs how the next token is minted, not"
            " how an existing one is read: these are opaque strings"
            " matched by lookup, so tokens already issued keep working"
            " at whatever width they were minted at."
        ),
        group="Authentication",
        level=SettingLevel.ADVANCED,
        min_value=16,
        max_value=64,
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
    )
)

# ── Adversarial red-team gate ───────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="red_team_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the adversarial red-team agent and its grounding"
            " checker run on. A model reference (`{provider, model_id}`)"
            " because a provider is a registered connection with its own"
            " credentials and endpoint, so a bare model id names no dispatch"
            " target. Named explicitly rather than borrowing another feature's"
            " connection: the adversary attacks the deliverable, so an operator"
            " chooses what it costs and where it runs. Unset leaves the gate"
            " unarmed and says so."
        ),
        group="Red Team",
        level=SettingLevel.ADVANCED,
    )
)

# ── LLM-backed security evaluation ──────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="llm_evaluator_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the LLM security evaluator judges unclassifiable"
            " actions on. A model reference (`{provider, model_id}`) because a"
            " provider is a registered connection with its own credentials and"
            " endpoint, so a bare model id names no dispatch target. Choose a"
            " connection from a different vendor family than the agents it"
            " judges, so a jailbreak of one family does not also cover its own"
            " reviewer; the evaluator warns when the families match. Unset"
            " leaves LLM fallback unarmed and the rule engine decides alone."
        ),
        group="LLM Fallback",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="safety_classifier_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the two-stage safety classifier runs on. A model"
            " reference (`{provider, model_id}`) because a provider is a"
            " registered connection with its own credentials and endpoint, so a"
            " bare model id names no dispatch target. The classifier reads"
            " attacker-controllable input, so the connection it runs on is an"
            " operator's explicit choice. Unset leaves the classifier unarmed"
            " and escalation falls back to human review."
        ),
        group="Safety Classifier",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.SECURITY,
        key="vision_verify_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the `llm_vision` deliverable verifier inspects"
            " screenshots on. A model reference (`{provider, model_id}`) because"
            " a provider is a registered connection with its own credentials and"
            " endpoint, so a bare model id names no dispatch target. Must name a"
            " multimodal model. Unset leaves the vision gate unbuilt rather than"
            " guessing which registered model can see."
        ),
        group="Vision Verify",
        level=SettingLevel.ADVANCED,
    )
)
