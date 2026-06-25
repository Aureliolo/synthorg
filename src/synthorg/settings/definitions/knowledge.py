"""Knowledge namespace setting definitions.

The knowledge substrate (document ingestion + retrieval over the memory
backend) is on by default and live-gated at its controller, so toggling
it takes effect with no restart. Knowledge has no model of its own: it
rides the embedding model that powers memory (see ``memory.embedder_model``).
"""

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
            " Knowledge uses the embedding model that powers memory. Read at"
            " startup (the substrate wires into the boot engine), so a change"
            " is restart-required."
        ),
        group="General",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)
