"""Knowledge namespace setting definitions.

The knowledge substrate (document ingestion + retrieval over the memory
backend) is on by default. Its retrieval surface has no model of its
own: it rides the embedding model that powers memory (see
``memory.embedder_model``). The optional synthesis step below adds a
separate completion model (see ``knowledge.synthesis_model``).

Also governs the substrate's optional generative-RAG (synthesis) step:
its enable flag, the provider + model the ``ask`` surface uses, the
synthesis strategy discriminator, and the per-answer chunk budget. The
substrate's retrieval surface is unaffected by these and stays available
regardless. Synthesis is on by default (opt-out), matching the
on-by-default posture: it is a user-initiated capability, not autonomous
spend, egress, or self-modification. It is functionally gated on a
configured model, so the ``ask`` surface 503s with a clear message until
one is set.
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
        key="enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for the knowledge substrate (document ingestion"
            " and retrieval). On by default; turning it off is advanced."
            " Knowledge uses the embedding model that powers memory. The"
            " substrate is ghost-wired at startup and the switch is enforced"
            " live per request at the knowledge tools, so toggling it takes"
            " effect on the next call with no restart."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether the knowledge ask surface (generative RAG: retrieve"
            " chunks then synthesise a grounded, citation-bound answer) is"
            " available. On by default; the synthesiser is ghost-wired and the"
            " ask surface is gated live on this flag, so toggling it takes"
            " effect with no restart. It still requires a synthesis model to be"
            " set before ask answers. Retrieval is unaffected."
        ),
        group="Synthesis",
        restart_required=False,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.KNOWLEDGE,
        key="synthesis_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the knowledge synthesis step uses, selected"
            " through the model picker (a `{provider, model_id}` reference)."
            " Must be set for the ask surface to answer; until then ask returns"
            " a configure-a-model error and retrieval stays available. A change"
            " rebuilds and swaps the synthesiser live with no restart. This is"
            " knowledge's own model, distinct from the embedding model (which"
            " powers retrieval) and decomposition."
        ),
        group="Synthesis",
        level=SettingLevel.ADVANCED,
        restart_required=False,
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
        restart_required=False,
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
        restart_required=False,
        min_value=1,
        max_value=KNOWLEDGE_SEARCH_MAX_LIMIT,
    )
)
