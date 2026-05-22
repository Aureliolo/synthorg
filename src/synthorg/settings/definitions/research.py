"""Research namespace setting definitions.

Governs the research subsystem: its master feature flag, the provider +
model used for the pipeline's LLM calls, and the pluggable strategy
discriminators. Strategy values are validated against
:class:`~synthorg.research.config.ResearchConfig` at wiring time.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Master switch for the research subsystem. When false the"
            " research service and tool are not wired, so agents cannot"
            " run research briefs."
        ),
        group="General",
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="provider",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the registered completion provider used for the"
            " research pipeline's LLM calls (planning / triage / synthesis)."
            " Empty selects the first registered provider."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier the research pipeline uses for its LLM calls."
            " Must be set (and the subsystem enabled) for research to wire"
            " at startup."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="query_planner",
        type=SettingType.STRING,
        default="llm",
        description="Discriminator selecting the query-planning strategy.",
        group="Strategies",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="credibility_triage",
        type=SettingType.STRING,
        default="hybrid",
        description=(
            "Discriminator selecting the credibility-triage strategy"
            " ('hybrid', 'heuristic', or 'llm')."
        ),
        group="Strategies",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="deduplicator",
        type=SettingType.STRING,
        default="lexical",
        description=(
            "Discriminator selecting the deduplication strategy"
            " ('lexical' or 'embedding')."
        ),
        group="Strategies",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="synthesizer",
        type=SettingType.STRING,
        default="llm",
        description="Discriminator selecting the synthesis strategy.",
        group="Strategies",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)
