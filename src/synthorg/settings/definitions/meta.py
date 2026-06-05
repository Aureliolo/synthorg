"""Meta namespace setting definitions.

Covers the meta-agent CI validator, proposal rate-limit guard, and
chief-of-staff outcome store defaults.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="ci_timeout_seconds",
        type=SettingType.INTEGER,
        default="300",
        description=(
            "Timeout for CI validation subprocess calls invoked by the"
            " meta-agent validator"
        ),
        group="Validation",
        level=SettingLevel.ADVANCED,
        min_value=30,
        max_value=600,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="proposal_rate_limit_max",
        type=SettingType.INTEGER,
        default="10",
        description=("Maximum meta-agent proposals accepted per rate-limit window"),
        group="Guards",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="outcome_store_default_limit",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "Default page size for chief-of-staff outcome-store queries"
            " when no explicit limit is provided"
        ),
        group="Chief of Staff",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="self_improvement",
        type=SettingType.JSON,
        default="{}",
        description=(
            "Runtime overrides for the SelfImprovementConfig model."
            "  Empty object means 'use code defaults'.  Keys are merged"
            " onto the default via model_copy."
        ),
        group="Self-Improvement",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="toolsmith_cycle_paused",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Pause flag for the toolsmith autonomous detection cycle. When"
            " True the periodic scheduler stays resident but every tick"
            " short-circuits, so the org stops proposing new tools without a"
            " restart -- used to halt self-extension during an incident."
        ),
        group="Self-Improvement",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.META,
        key="scorecard_history_dir",
        type=SettingType.STRING,
        default="",
        description=(
            "Filesystem directory the golden-company benchmark records"
            " per-run scorecard summaries into. The learning-curve"
            " endpoint and the in-app self-improvement loop read the"
            " curve from here. Empty means 'no benchmark history"
            " configured' and yields an empty curve."
        ),
        group="Self-Improvement",
        level=SettingLevel.ADVANCED,
    )
)
