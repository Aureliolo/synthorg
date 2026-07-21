"""Procedural-memory setting definitions (memory namespace, Procedural group).

Procedural memory turns a recovered failure into a reusable skill. Before
these keys existed the whole pipeline was configurable only through company
YAML, so an operator had no way to pause it, retune its quality floor, or
point it at a ``SKILL.md`` directory.

``procedural_enabled`` and ``procedural_min_confidence`` are resolved per
capture, so a change takes effect on the next task. The remaining knobs are
baked into the frozen ``ProceduralMemoryConfig`` the proposer holds and
therefore apply on the next restart.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="procedural_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master switch for procedural memory. When False the proposer"
            " stays constructed but every post-execution capture"
            " short-circuits, so no skill is proposed and no LLM call is"
            " made. Resolved per capture."
        ),
        group="Procedural",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="procedural_min_confidence",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Discard procedural proposals the proposer rates below this"
            " confidence. Raising it trades skill volume for skill quality."
            " Resolved per capture."
        ),
        group="Procedural",
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    # lint-allow: restart-required -- baked into the frozen
    # ProceduralMemoryConfig the proposer holds; a change applies on the
    # next process start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="procedural_temperature",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            "Sampling temperature for the procedural proposer. Baked into"
            " the proposer config at startup, so a change applies on the"
            " next restart."
        ),
        group="Procedural",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=2.0,
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- baked into the frozen
    # ProceduralMemoryConfig the proposer holds; a change applies on the
    # next process start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="procedural_max_tokens",
        type=SettingType.INTEGER,
        default="1500",
        description=(
            "Response token budget for the procedural proposer. Baked into"
            " the proposer config at startup, so a change applies on the"
            " next restart."
        ),
        group="Procedural",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=32_000,
        restart_required=True,
    )
)

_r.register(
    # lint-allow: restart-required -- baked into the frozen
    # ProceduralMemoryConfig the pipeline holds; a change applies on the
    # next process start.
    SettingDefinition(
        namespace=SettingNamespace.MEMORY,
        key="procedural_skill_md_directory",
        type=SettingType.STRING,
        default=None,
        description=(
            "Directory for SKILL.md materialisation. When set, accepted"
            " proposals are also written as portable Markdown files for"
            " git-native versioning. Empty keeps skills in the memory"
            " backend only. Baked in at startup, so a change applies on"
            " the next restart."
        ),
        group="Procedural",
        level=SettingLevel.ADVANCED,
        restart_required=True,
    )
)
