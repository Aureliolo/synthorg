"""Knowledge namespace setting definitions.

Governs the knowledge substrate's optional generative-RAG (synthesis) step:
its enable flag, the provider + model the ``ask`` surface uses, the synthesis
strategy discriminator, and the per-answer chunk budget. The substrate's
retrieval surface is unaffected by these and stays available regardless.

Synthesis is on by default (opt-out), matching the on-by-default posture:
it is a user-initiated capability, not autonomous spend, egress, or
self-modification. It is functionally gated on a configured model, so the
``ask`` surface 503s with a clear message until one is set.
"""

from synthorg.knowledge.constants import (
    KNOWLEDGE_SEARCH_MAX_LIMIT,
    KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS,
)
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the knowledge ask surface (generative RAG: retrieve"
            " chunks then synthesise a grounded, citation-bound answer) is"
            " wired. On by default; it still requires a synthesis model to be"
            " set before the ask surface answers. Retrieval is unaffected."
        ),
        group="Synthesis",
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_provider",
        type=SettingType.STRING,
        default="",
        description=(
            "Name of the registered completion provider the knowledge"
            " synthesis step uses. Empty selects the first registered"
            " provider."
        ),
        group="Synthesis",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_model",
        type=SettingType.STRING,
        default="",
        description=(
            "Model identifier the knowledge synthesis step uses. Must be set"
            " (and synthesis enabled) for the ask surface to wire at startup;"
            " until then ask returns a configure-a-model error and retrieval"
            " stays available. This is knowledge's own model, distinct from"
            " the embedding model (which powers retrieval) and decomposition."
        ),
        group="Synthesis",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_synthesizer",
        type=SettingType.STRING,
        default="llm",
        description="Discriminator selecting the knowledge synthesis strategy.",
        group="Synthesis",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        validator_pattern=r"^[a-z][a-z0-9_]*$",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_max_chunks",
        type=SettingType.INTEGER,
        default=str(KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS),
        description=(
            "Number of top-ranked retrieved chunks presented to the synthesis"
            " LLM. Higher widens grounding (more context, higher token cost);"
            " lower tightens it."
        ),
        group="Synthesis",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1,
        max_value=KNOWLEDGE_SEARCH_MAX_LIMIT,
    )
)
