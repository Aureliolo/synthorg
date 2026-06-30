"""Research namespace setting definitions.

Governs the research subsystem: its master feature flag, the provider +
model used for the pipeline's LLM calls, and the pluggable strategy
discriminators. Strategy values are validated against
:class:`~synthorg.research.config.ResearchConfig` at wiring time.
"""

from synthorg.research.constants import (
    RESEARCH_DEDUP_JACCARD_THRESHOLD,
    RESEARCH_DEFAULT_PER_QUERY_LIMIT,
    RESEARCH_HYBRID_PREFILTER_FACTOR,
    RESEARCH_TRIAGE_BATCH_SIZE,
)
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for the research subsystem. On by default: the"
            " service + tool are ghost-wired at startup whenever a research"
            " model is configured, and the switch is enforced live per request"
            " at the research tools, so toggling it takes effect on the next"
            " call with no restart. When off, the research tools return a"
            " disabled error."
        ),
        group="General",
        restart_required=False,
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
        restart_required=False,
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
            " Must be set for the research service to be wired; a change"
            " rebuilds and swaps the service live with no restart."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=False,
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
        restart_required=False,
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
        restart_required=False,
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
        restart_required=False,
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
        restart_required=False,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="triage_batch_size",
        type=SettingType.INTEGER,
        default=str(RESEARCH_TRIAGE_BATCH_SIZE),
        description=(
            "Number of retrieved items grouped into each LLM"
            " credibility-triage call. Higher cuts provider round-trips"
            " (lower cost on large result sets); lower reduces per-call"
            " token pressure on weaker models."
        ),
        group="Tuning",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="hybrid_prefilter_factor",
        type=SettingType.FLOAT,
        default=str(RESEARCH_HYBRID_PREFILTER_FACTOR),
        description=(
            "Fraction of a brief's min_credibility a source's heuristic"
            " score must reach before the hybrid strategy escalates it to"
            " LLM triage. Lower widens the LLM pass (higher quality, higher"
            " cost); higher narrows it."
        ),
        group="Tuning",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="dedup_similarity_threshold",
        type=SettingType.FLOAT,
        default=str(RESEARCH_DEDUP_JACCARD_THRESHOLD),
        description=(
            "Similarity at or above which two retrieved items are collapsed"
            " as near-duplicates (token-shingle Jaccard for the lexical"
            " deduplicator, cosine for the embedding one). Lower collapses"
            " more aggressively; higher is more permissive."
        ),
        group="Tuning",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=0.1,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.RESEARCH,
        key="per_query_limit",
        type=SettingType.INTEGER,
        default=str(RESEARCH_DEFAULT_PER_QUERY_LIMIT),
        description=(
            "Default number of candidate items each retrieval source"
            " returns per sub-query. The deployment-level floor applied"
            " when a brief does not override it."
        ),
        group="Tuning",
        level=SettingLevel.ADVANCED,
        restart_required=False,
        min_value=1,
        max_value=200,
    )
)
