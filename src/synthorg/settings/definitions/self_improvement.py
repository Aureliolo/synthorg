"""Self-improvement namespace setting definitions.

The self-improvement meta-loop lets the system rewrite itself (config,
prompts, architecture, code, tools), so every switch here defaults off
and is ``restart_required``: enabling one wires a boot-time loop and, for
code modification, reads GitHub credentials at startup. These flags
overlay onto :class:`~synthorg.meta.config.SelfImprovementConfig` at load
time and are the single source of truth for the flags and models below.
The deep structural tuning (schedule, rollout, regression, guards) stays
in the ``meta.self_improvement`` JSON setting.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()
_NS = SettingNamespace.SELF_IMPROVEMENT
_GROUP = "Self-Improvement"


def _flag(key: str, description: str) -> None:
    """Register an off-by-default, restart-required self-improvement flag.

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
            restart_required=True,
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
        restart_required=True,
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
    " GitHub credentials at startup.",
)
_flag(
    "tool_creation_enabled",
    "Allow the self-extending toolkit (toolsmith) to propose new tools when"
    " the meta-loop is on.",
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="analysis_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for self-improvement proposal analysis. Empty"
            " keeps the built-in default; set it to override."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=_NS,
        key="code_modification_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier for code-modification proposals. Empty keeps"
            " the built-in default; set it to override."
        ),
        group=_GROUP,
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)
