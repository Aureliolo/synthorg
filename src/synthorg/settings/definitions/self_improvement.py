"""Self-improvement namespace setting definitions.

The self-improvement meta-loop lets the system rewrite itself (config,
prompts, architecture, code, tools), so every switch here defaults off.
The meta-loop re-reads these flags live (the master switch and strategy
toggles per cycle, the toolsmith gate per proposal, the models per call),
so toggling one takes effect at once. ``code_modification_enabled`` also
requires GitHub credentials in the structural blob; without them the next
load refuses the config and falls back with the flag forced off, which is
the failure an operator sees rather than a silently un-applied switch.
These flags overlay onto :class:`~synthorg.meta.config.SelfImprovementConfig`
at load time and are the single source of truth for the flags and models
below. The deep structural tuning (schedule, rollout, regression, guards)
stays in the ``meta.self_improvement`` JSON setting.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()
_NS = SettingNamespace.SELF_IMPROVEMENT
_GROUP = "Self-Improvement"


def _flag(key: str, description: str) -> None:
    """Register an off-by-default self-improvement flag.

    Args:
        key: The setting key within the self-improvement namespace.
        description: Human-readable description for the /settings UI.
    """
    _r.register(
        SettingDefinition(
            namespace=_NS,
            key=key,
            type=SettingType.BOOLEAN,
            default="false",
            description=description,
            group=_GROUP,
            level=SettingLevel.ADVANCED,
        )
    )


_flag(
    "enabled",
    "Master switch for the self-improvement meta-loop. When on, the system"
    " can propose changes to itself. Off by default (a bad proposal can"
    " break a running org).",
)
_flag(
    "chief_of_staff_enabled",
    "Enable the Chief-of-Staff persona inside the self-improvement loop"
    " (distinct from the conversational chat capabilities).",
)
# config_tuning is the cheapest, lowest-risk proposal type, so it is the one
# self-improvement capability pre-enabled once the master switch is on. It has
# no effect while ``self_improvement.enabled`` is off (the meta-loop never
# runs), so a "true" default here does not loosen the off-by-default posture.
_r.register(
    SettingDefinition(
        namespace=_NS,
        key="config_tuning_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Allow config-tuning proposals once the meta-loop is on. Has no"
            " effect while self_improvement.enabled is off."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)
_flag(
    "architecture_proposals_enabled",
    "Allow architecture-change proposals when the meta-loop is on.",
)
_flag(
    "prompt_tuning_enabled",
    "Allow prompt-tuning proposals when the meta-loop is on.",
)
_flag(
    "code_modification_enabled",
    "Allow code-modification proposals when the meta-loop is on. Requires"
    " GitHub credentials in the meta.self_improvement blob; without them"
    " the next config load refuses and forces this back off.",
)
_flag(
    "tool_creation_enabled",
    "Allow the self-extending toolkit (toolsmith) to propose new tools when"
    " the meta-loop is on.",
)

# An empty allowlist is deny-all, so the toolsmith cannot be enabled without
# at least one capability tag. tool_creation_enabled is held off until this
# is set, rather than failing the whole self-improvement config.
_r.register(
    SettingDefinition(
        namespace=_NS,
        key="tool_creation_allowed_capabilities",
        type=SettingType.JSON,
        default="[]",
        description=(
            "Capability tags (``domain:action``) the self-extending toolkit"
            " may author tools for. Required to enable tool creation; an empty"
            " list is deny-all and keeps tool creation off. Re-read live per"
            " proposal."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="analysis_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for self-improvement proposal analysis, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the built-in default. Read live per analysis call."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="code_modification_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model for code-modification proposals, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Empty keeps the built-in default. Read live per generation"
            " (the capability itself stays restart-required)."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
    )
)
