# module-kind: declarative
"""Output-style policy setting definitions (output_style namespace).

The hard guardrail and the soft house-style layer are both driven by the active
pack; these settings select and gate them. All are Category-1 (DB > env >
default) and hot-reloadable: a change rebuilds the ``OutputStylePolicyService``
and re-binds the ambient service + house-style provider on the next boundary
check and prompt build (see ``OutputStyleSettingsSubscriber``), no restart.

Disabling ``enabled``, enabling ``shadow_mode``, or broadening ``exemptions``
weakens the guardrail, so those writes route through the security-write
governance guardrail (confirm + reason + actor).
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_GROUP = "Output Style"

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OUTPUT_STYLE,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for the deterministic output-style guardrail that "
            "rejects or rewrites agent output violating a hard rule (e.g. the "
            "em-dash ban) at every output boundary."
        ),
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OUTPUT_STYLE,
        key="shadow_mode",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "When on, every rule is forced to shadow: violations are surfaced "
            "and audited but never block or rewrite. Use for an observation "
            "period before enforcing."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OUTPUT_STYLE,
        key="pack",
        type=SettingType.STRING,
        default="default",
        description=(
            "Active output-style pack (a built-in name, or a user pack under "
            "~/.synthorg/output-style-packs). Holds the house-style directives "
            "and the hard rules."
        ),
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OUTPUT_STYLE,
        key="house_style_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the soft house-style directive block is injected into "
            "agent system prompts (the ask; the hard guardrail is the enforce)."
        ),
        group=_GROUP,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.OUTPUT_STYLE,
        key="exemptions",
        type=SettingType.JSON,
        default="[]",
        description=(
            "Operator-authored sanctioned exemptions (JSON array of "
            "{rule_id, scope_kind, match, reason}). An agent is granted an "
            "exemption only when its output context matches a sanctioned scope "
            "(codebase path, task type, project, department, role, or "
            "deliverable tag); agents never self-grant."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)
